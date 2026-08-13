import logging
import html
import unicodedata
from datetime import datetime

from src.config import Config
from src.poller import PollAuthError, PollError, missing_cookie_fields
from src.health_db import HealthDatabase

logger = logging.getLogger(__name__)


class HealthReporter:
    """Build and deliver read-only system health reports."""

    def __init__(self, config: Config, poller, account, state, notifier, format_position,
                 database: HealthDatabase | None = None):
        self._config = config
        self._poller = poller
        self._account = account
        self._state = state
        self._notifier = notifier
        self._format_position = format_position
        self._database = database

    async def send_hourly_report(self):
        report = await self._build_report(include_positions=True, notify_auth_failures=True)
        logger.info("[Health] %s", report.replace("\n", " | "))
        balance = getattr(self, "_last_balance", None)
        checked_at = getattr(self, "_last_checked_at", None)
        if self._database is not None and balance is not None and checked_at is not None:
            try:
                self._database.record_balance(checked_at, balance)
            except Exception:
                logger.exception("[Health] Failed to persist account balance")
        await self._notifier.notify_health_check(self._format_table(report))

    async def build_status_report(self, paused: bool) -> str:
        report = await self._build_report(include_positions=False, notify_auth_failures=False)
        return f"📊 <b>SYSTEM STATUS</b>\n{self._format_table('Status: ' + ('PAUSED' if paused else 'RUNNING') + '\n' + report)}"

    @staticmethod
    def _format_table(report: str) -> str:
        rows = []
        for line in report.splitlines():
            label, separator, value = line.partition(":")
            rows.append((label, value.strip() if separator else ""))
        width = max(HealthReporter._display_width(label) for label, _ in rows)
        body = "\n".join(
            f"{html.escape(HealthReporter._pad_to_width(label, width))}  {html.escape(value)}"
            for label, value in rows
        )
        return f"<pre>{body}</pre>"

    @staticmethod
    def _display_width(value: str) -> int:
        """Approximate Telegram monospace display width for mixed CJK text."""
        return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
                   for char in value)

    @classmethod
    def _pad_to_width(cls, value: str, width: int) -> str:
        return value + " " * max(0, width - cls._display_width(value))

    async def _build_report(self, include_positions: bool, notify_auth_failures: bool) -> str:
        self._last_balance = None
        self._last_checked_at = datetime.now().astimezone()
        checked_at = self._last_checked_at.strftime("%Y-%m-%d %H:%M:%S %Z")
        lines = [f"Time: {checked_at}"]

        for leader_cfg in self._config.leaders:
            if not leader_cfg.enabled:
                continue
            state = self._state.leader_states.get(leader_cfg.name)
            try:
                positions = await self._poller.fetch_positions(leader_cfg.portfolio_id)
                lines.append(
                    f"{leader_cfg.name}: {self._cookie_status(positions)}; "
                    f"leader positions: {len(positions)}"
                )
                if state and state.consecutive_errors > 0:
                    lines.append(f"Poll [{leader_cfg.name}]: errors={state.consecutive_errors}")
            except PollAuthError as e:
                lines.append(f"{leader_cfg.name}: FAILED (auth)")
                logger.error("[Health] Cookie check failed for %s: %s", leader_cfg.name, e)
                if notify_auth_failures:
                    await self._notifier.notify_cookie_expired(
                        self._config.binance_source.type
                    )
            except PollError as e:
                lines.append(f"{leader_cfg.name}: FAILED ({e})")
                logger.error("[Health] Leader check failed for %s: %s", leader_cfg.name, e)
                if notify_auth_failures:
                    await self._notifier.notify_error(
                        f"Hourly leader check failed: {leader_cfg.name} - {e}"
                    )

        try:
            balance = await self._account.get_total_margin()
            self._last_balance = balance
            positions = await self._account.get_positions()
            lines.append(f"Balance: {balance:.2f}")
            lines.append(f"positions: {len(positions)}")
            if include_positions:
                if positions:
                    lines.extend(f"Account position: {self._format_position(position)}" for position in positions)
                else:
                    lines.append("Account positions: none")
        except Exception as e:
            lines.append(f"Bitget: FAILED ({e})")
            logger.exception("[Health] Account check failed: %s", e)
            if notify_auth_failures:
                await self._notifier.notify_error(f"Hourly account check failed: {e}")

        lines.append(f"Local mirror positions: {len(self._state.mirror_positions)}")
        return "\n".join(lines)

    def _cookie_status(self, positions: list) -> str:
        source_type = self._config.binance_source.type.lower()
        if source_type == "browser":
            return "BROWSER AUTH OK" if positions else "BROWSER AUTH OK; no positions returned"
        if source_type == "hybrid":
            return "HYBRID HTTP OK" if positions else "HYBRID HTTP OK; no positions returned"
        missing = missing_cookie_fields(self._config.binance_web.cookie)
        if missing:
            return f"COOKIE INCOMPLETE (missing {', '.join(missing)})"
        return "API OK" if positions else "API OK; no positions returned"
