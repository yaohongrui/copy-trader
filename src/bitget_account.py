import logging
from decimal import Decimal
from time import time

from src.bitget_client import BitgetClient

logger = logging.getLogger(__name__)


class BitgetAccount:
    """Account adapter for Bitget UTA cross-margin USDT futures."""

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.client = BitgetClient(api_key, api_secret, api_passphrase)
        self._balance_cache = Decimal(0)
        self._cache_time = 0.0
        self._cache_ttl = 10.0

    async def start(self):
        await self.client.start()
        existing_positions = await self.client.get_positions()
        if existing_positions:
            logger.warning(
                "Bitget account has %d position(s); holding mode was not changed. "
                "Ensure the account is already in one-way mode before trading.",
                len(existing_positions),
            )
        else:
            await self.client.set_hold_mode("one_way_mode")
            logger.info("Bitget holding mode set: one-way")

    async def stop(self):
        await self.client.stop()

    async def get_total_margin(self) -> Decimal:
        now = time()
        if now - self._cache_time < self._cache_ttl:
            return self._balance_cache
        assets = await self.client.get_account_assets()
        # UTA uses one shared collateral pool; usdtEquity includes PnL and is
        # the balance relevant to the sizing formula.
        equity = Decimal(str(assets.get("usdtEquity", "0")))
        self._balance_cache, self._cache_time = equity, now
        return equity

    async def get_positions(self) -> list[dict]:
        positions = await self.client.get_positions()
        normalized = []
        for item in positions:
            quantity = self._decimal(self._first(item, "total", "size"))
            if quantity == 0:
                continue
            hold_side = str(self._first(item, "holdSide", "posSide") or "").lower()
            signed = -abs(quantity) if hold_side == "short" else abs(quantity)
            # UTA can return a zero placeholder for the first alias while
            # returning the actual value under another alias.
            entry = self._decimal(self._first_nonzero(
                item, "avgPrice", "openPriceAvg", "entryPrice", "openPrice",
                "avgOpenPrice", "averageOpenPrice",
            ))
            mark = self._decimal(self._first_nonzero(
                item, "markPrice", "mark_price", "currentPrice",
            ))
            unrealized = self._decimal(self._first_nonzero(
                item, "unrealisedPnl", "unrealizedPL", "unrealizedPnl",
                "unrealizedPnL", "unrealizedPNL", "upl",
            ))
            margin = self._decimal(self._first_nonzero(
                item, "positionBalance", "marginSize", "positionMargin", "margin",
            ))
            if margin <= 0:
                leverage = self._decimal(self._first(item, "leverage"))
                if entry > 0 and leverage > 0:
                    margin = abs(quantity) * entry / leverage
            profit_rate = self._decimal(self._first_nonzero(item, "profitRate"))
            # Bitget documents profitRate as the position's profit rate.  Use
            # it directly so ROI follows the exchange calculation (including
            # its treatment of fees/funding); retain a fallback for older
            # responses that do not include it.
            roi = profit_rate * 100 if profit_rate != 0 else (
                unrealized / margin * 100 if margin > 0 else Decimal(0)
            )
            normalized.append({
                "contract": item.get("symbol", ""),
                "size": str(signed),
                "entry_price": str(entry),
                "mark_price": str(mark),
                "unrealized_pnl": str(unrealized),
                "margin": str(margin),
                "roi_pct": str(roi),
            })
        return normalized

    @staticmethod
    def _first(item: dict, *keys: str):
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return "0"

    @classmethod
    def _first_nonzero(cls, item: dict, *keys: str):
        """Return the first parseable, non-zero value among API aliases."""
        fallback = "0"
        for key in keys:
            value = item.get(key)
            if value in (None, ""):
                continue
            fallback = value
            try:
                if Decimal(str(value)) != 0:
                    return value
            except (ArithmeticError, ValueError):
                continue
        return fallback

    @staticmethod
    def _decimal(value) -> Decimal:
        try:
            return Decimal(str(value or "0"))
        except (ArithmeticError, ValueError):
            return Decimal(0)
