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

    async def execute(self, signal: TradeSignal, quantity: Decimal, limit_price: Decimal | None = None) -> OrderResult:
        try:
            instrument = await self._account.client.get_instrument(signal.symbol)
            executable_qty = Decimal(self._account.client.format_quantity(quantity, instrument))
            is_open = signal.signal_type in (SignalType.OPEN, SignalType.INCREASE)
            if is_open and signal.symbol not in self._configured_leverage:
                leverage = await self._account.client.set_leverage(signal.symbol, self.TARGET_LEVERAGE)
                self._configured_leverage[signal.symbol] = leverage
                logger.info("Bitget leverage set: %s = %dx", signal.symbol, leverage)

            client_oid = f"copy-{uuid4().hex[:20]}"
            if limit_price is not None:
                response = await self._account.client.place_limit_order(
                    symbol=signal.symbol, qty=executable_qty, side=signal.side,
                    price=limit_price, reduce_only=False, client_oid=client_oid,
                )
            else:
                response = await self._account.client.place_market_order(
                    symbol=signal.symbol, qty=executable_qty, side=signal.side,
                    reduce_only=not is_open, client_oid=client_oid,
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
                if limit_price is not None and order_id:
                    # 限价单已提交但无法确认状态时，按未成交处理并交给
                    # coordinator 登记，避免后续信号重复开仓。
                    return OrderResult(success=False, order_id=order_id,
                                       avg_price=limit_price, pending=True,
                                       error="Bitget order details unavailable")
                return OrderResult(success=False, error="Bitget order details unavailable")

            status = str(details.get("orderStatus", "")).lower()
            filled_qty = Decimal(str(details.get("cumExecQty", "0")))
            avg_price = Decimal(str(details.get("avgPrice", "0")))
            resolved_order_id = str(details.get("orderId", order_id or ""))

            if limit_price is not None and status in {"new", "live", "partially_filled"}:
                return OrderResult(success=False, order_id=resolved_order_id or None,
                                   avg_price=avg_price if avg_price > 0 else limit_price, pending=True,
                                   error="Limit order pending")
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
