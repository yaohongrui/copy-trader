import asyncio
import logging

import aiohttp

from src.config import TelegramConfig

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: TelegramConfig):
        self._cfg = cfg
        self._session: aiohttp.ClientSession | None = None

    @property
    def can_receive_commands(self) -> bool:
        return bool(self._cfg.enabled and self._cfg.bot_token and self._cfg.chat_id and self._session)

    def is_authorized_chat(self, chat_id) -> bool:
        return str(chat_id) == str(self._cfg.chat_id)

    async def start(self):
        if not self._cfg.enabled:
            logger.warning("Telegram notifications disabled")
            return
        if not self._cfg.bot_token or not self._cfg.chat_id:
            logger.error("Telegram notifications enabled but bot_token/chat_id is missing")
            return
        if self._cfg.enabled:
            self._session = aiohttp.ClientSession()
            logger.info("Telegram notifier started")

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, message: str) -> bool:
        if not self._cfg.enabled or not self._session:
            logger.warning("Telegram message skipped: notifier is not ready")
            return False

        url = f"https://api.telegram.org/bot{self._cfg.bot_token}/sendMessage"
        payload = {"chat_id": self._cfg.chat_id, "text": message, "parse_mode": "HTML"}

        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200 or not isinstance(body, dict) or not body.get("ok", False):
                    logger.warning(
                        "Telegram send failed: http_status=%s response=%s",
                        resp.status, body,
                    )
                    return False
                logger.info("Telegram message sent successfully")
                return True
        except Exception as e:
            logger.warning("Telegram notification error: %s", e)
            return False

    async def get_updates(self, offset: int = 0, timeout: int = 20) -> list[dict]:
        """Receive Telegram commands for the configured chat via long polling."""
        if not self.can_receive_commands:
            return []

        url = f"https://api.telegram.org/bot{self._cfg.bot_token}/getUpdates"
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout + 5),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200 or not isinstance(body, dict) or not body.get("ok", False):
                    logger.warning(
                        "Telegram getUpdates failed: http_status=%s response=%s",
                        resp.status, body,
                    )
                    return []
                result = body.get("result", [])
                return result if isinstance(result, list) else []
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram getUpdates error: %s", e)
            return []

    async def notify_trade(
        self,
        leader: str,
        symbol: str,
        side: str,
        qty: str,
        signal_type: str,
        avg_price: str | None = None,
        order_id: str | None = None,
    ):
        msg = (
            f"✅ <b>[Trade Executed]</b>\n"
            f"Leader: {leader}\n"
            f"Symbol: {symbol}\n"
            f"Type: {signal_type.upper()}\n"
            f"Side: {side}\n"
            f"Quantity: {qty}"
        )
        if avg_price and avg_price != "0":
            msg += f"\nPrice: {avg_price}"
        if order_id:
            msg += f"\nOrder ID: {order_id}"
        await self.send(msg)

    async def notify_health_check(self, message: str):
        await self.send(f"🩺 <b>[Hourly Health Check]</b>\n{message}")

    async def notify_error(self, error: str):
        await self.send(f"❌ <b>[Error]</b>\n{error}")

    async def notify_cookie_expired(self, source_type: str = "http"):
        if source_type.lower() == "hybrid":
            await self.send(
                "⚠️ <b>[Binance Authentication Failed]</b>\n"
                "HTTP authentication failed and automatic browser-profile recovery did not succeed. "
                "Complete Binance login/verification through VNC, then restart copy-trader."
            )
            return
        await self.send(
            "⚠️ <b>[Cookie Expired]</b>\n"
            "Cookie has expired. Please update config and reload service:\n"
            "<code>sudo systemctl reload copy-trader</code>"
        )

    async def notify_skipped(self, leader: str, symbol: str, signal_type: str, side: str, reason: str):
        """通知订单被跳过"""
        await self.send(
            f"⏭️ <b>[Order Skipped]</b>\n"
            f"Leader: {leader}\n"
            f"Symbol: {symbol}\n"
            f"Type: {signal_type.upper()} {side}\n"
            f"Reason: {reason}"
        )
