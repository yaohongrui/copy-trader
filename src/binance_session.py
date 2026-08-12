"""Browser-backed renewal of the fast HTTP Binance position source."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.binance_auth import BinanceAuthError, BinanceBrowserAuth, BrowserAuthConfig
from src.config import BinanceBrowserConfig, BinanceSessionConfig
from src.poller import PollAuthError, Poller


logger = logging.getLogger(__name__)


class BinanceSessionKeeper:
    """Refresh HTTP credentials from a persistent browser profile when needed."""

    def __init__(
        self,
        poller: Poller,
        browser_config: BinanceBrowserConfig,
        session_config: BinanceSessionConfig,
        portfolio_id: str,
    ):
        self._poller = poller
        self._browser_config = browser_config
        self._session_config = session_config
        self._portfolio_id = portfolio_id
        self._refresh_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._keepalive_loop(), name="binance_session_keepalive")

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def reload(
        self, browser_config: BinanceBrowserConfig, session_config: BinanceSessionConfig
    ) -> None:
        if browser_config != self._browser_config:
            raise ValueError("Changing binance_source.browser settings requires restarting the service")
        if session_config.refresh_interval_hours <= 0:
            raise ValueError("binance_source.session.refresh_interval_hours must be positive")
        self._session_config = session_config

    async def refresh(self, portfolio_id: str | None = None) -> None:
        """Refresh once, serializing concurrent HTTP authentication recoveries."""
        async with self._refresh_lock:
            auth = BinanceBrowserAuth(BrowserAuthConfig(
                profile_dir=Path(self._browser_config.profile_dir),
                headless=self._browser_config.headless,
                timeout_ms=self._browser_config.timeout_ms,
            ))
            try:
                await auth.start()
                new_auth = await auth.refresh_http_auth(
                    portfolio_id or self._portfolio_id, self._poller.auth_config
                )
                self._poller.reload(new_auth)
                logger.info("Binance HTTP authentication refreshed from browser profile")
            except BinanceAuthError:
                raise
            except Exception as exc:
                raise BinanceAuthError(f"Browser session refresh failed: {exc}") from exc
            finally:
                await auth.stop()

    async def _keepalive_loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(self._session_config.refresh_interval_hours * 3600)
                if self._stopped:
                    return
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A valid HTTP session must keep running even if the background
                # browser refresh needs an interactive Binance verification.
                logger.warning("Binance browser keepalive failed: %s", exc)


class HybridBinancePositionSource:
    """Fast HTTP polling with browser recovery after an authentication failure."""

    def __init__(
        self,
        poller: Poller,
        browser_config: BinanceBrowserConfig,
        session_config: BinanceSessionConfig,
        fallback_portfolio_id: str,
    ):
        self._poller = poller
        self._keeper = BinanceSessionKeeper(
            poller, browser_config, session_config, fallback_portfolio_id
        )
        self._empty_refresh_attempted: set[str] = set()

    async def start(self) -> None:
        await self._poller.start()
        # Binance can return HTTP 200 with an empty data list when the copied
        # request headers are stale. Refresh before the first reconciliation so
        # that response is not treated as a real leader close-out.
        try:
            await self._keeper.refresh()
            logger.info("Binance HTTP authentication refreshed at startup")
        except Exception as exc:
            logger.warning(
                "Initial Binance browser refresh failed; using configured HTTP credentials: %s",
                exc,
            )
        await self._keeper.start()

    async def stop(self) -> None:
        await self._keeper.stop()
        await self._poller.stop()

    def reload(self, browser_config: BinanceBrowserConfig, session_config: BinanceSessionConfig, web_config) -> None:
        self._poller.reload(web_config)
        self._keeper.reload(browser_config, session_config)

    async def fetch_positions(self, portfolio_id: str):
        try:
            positions = await self._poller.fetch_positions(portfolio_id)
            if positions:
                self._empty_refresh_attempted.discard(portfolio_id)
                return positions

            # Binance may return code=000000 with data=[] when copied web
            # headers have gone stale. Refresh once for this empty episode,
            # then let the coordinator's consecutive-empty guard decide.
            if portfolio_id not in self._empty_refresh_attempted:
                self._empty_refresh_attempted.add(portfolio_id)
                logger.warning(
                    "Binance returned an empty position list for %s; refreshing browser authentication",
                    portfolio_id,
                )
                await self._keeper.refresh(portfolio_id)
                positions = await self._poller.fetch_positions(portfolio_id)
                if positions:
                    self._empty_refresh_attempted.discard(portfolio_id)
            return positions
        except PollAuthError as http_error:
            logger.warning("HTTP Binance authentication failed; attempting browser recovery")
            try:
                await self._keeper.refresh(portfolio_id)
                return await self._poller.fetch_positions(portfolio_id)
            except Exception as browser_error:
                raise PollAuthError(
                    f"HTTP authentication failed and browser recovery failed: {browser_error}"
                ) from http_error
        except BinanceAuthError as browser_error:
            raise PollAuthError(
                f"Browser authentication unavailable after empty position response: {browser_error}"
            ) from browser_error
