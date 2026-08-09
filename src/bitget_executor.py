import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from src.bitget_account import BitgetAccount
from src.bitget_client import BitgetAPIError
from src.models import OrderResult, SignalType, TradeSignal

logger = logging.getLogger(__name__)


class BitgetExecutor:
    TARGET_LEVERAGE = 50

    def __init__(self, account: BitgetAccount):
        self._account = account
        self._configured_leverage: dict[str, int] = {}

    async def execute(self, signal: TradeSignal, quantity: Decimal) -> OrderResult:
        try:
            instrument = await self._account.client.get_instrument(signal.symbol)
            executable_qty = Decimal(self._account.client.format_quantity(quantity, instrument))
            is_open = signal.signal_type in (SignalType.OPEN, SignalType.INCREASE)
            if is_open and signal.symbol not in self._configured_leverage:
                leverage = await self._account.client.set_leverage(signal.symbol, self.TARGET_LEVERAGE)
                self._configured_leverage[signal.symbol] = leverage
                logger.info("Bitget leverage set: %s = %dx", signal.symbol, leverage)

            client_oid = f"copy-{uuid4().hex[:20]}"
            response = await self._account.client.place_market_order(
                symbol=signal.symbol,
                qty=executable_qty,
                side=signal.side,
                reduce_only=not is_open,
                client_oid=client_oid,
            )
            order_id = str(response.get("orderId", ""))
            details = None
            for _ in range(8):
                try:
                    details = await self._account.client.get_order_info(
                        order_id=order_id or None,
                        client_oid=client_oid if not order_id else None,
                    )
                    status = str(details.get("orderStatus", "")).lower()
                    if status in {"filled", "partially_filled", "cancelled"}:
                        break
                except BitgetAPIError:
                    # The order detail endpoint can briefly lag placement.
                    pass
                await asyncio.sleep(0.25)

            if not details:
                return OrderResult(success=False, error="Bitget order details unavailable")

            status = str(details.get("orderStatus", "")).lower()
            filled_qty = Decimal(str(details.get("cumExecQty", "0")))
            avg_price = Decimal(str(details.get("avgPrice", "0")))
            resolved_order_id = str(details.get("orderId", order_id or ""))

            if status not in {"filled", "partially_filled"} or filled_qty <= 0:
                return OrderResult(
                    success=False,
                    order_id=resolved_order_id or None,
                    error=f"Bitget order status={status}, filled_qty={filled_qty}",
                )

            logger.info(
                "Bitget order filled: %s %s %s requested=%s filled=%s avg_price=%s id=%s",
                signal.signal_type.value, signal.side, signal.symbol,
                executable_qty, filled_qty, avg_price, resolved_order_id,
            )
            return OrderResult(
                success=True,
                order_id=resolved_order_id or None,
                filled_qty=filled_qty,
                avg_price=avg_price,
            )
        except BitgetAPIError as exc:
            logger.error("Bitget order failed for %s: %s", signal.symbol, exc)
            return OrderResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("Unexpected Bitget order failure for %s", signal.symbol)
            return OrderResult(success=False, error=str(exc))
