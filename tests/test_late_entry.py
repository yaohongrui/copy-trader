import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from src.bitget_client import BitgetAPIError, BitgetClient
from src.bitget_executor import BitgetExecutor
from src.config import LeaderConfig, RiskConfig
from src.coordinator import Coordinator
from src.models import OrderResult, SignalType, TradeSignal


def make_signal(signal_type, *, side="buy", symbol="BTCUSDT", position_side="BOTH",
                quantity=Decimal("1"), leader_entry_price=None, **kwargs):
    return TradeSignal(
        signal_type=signal_type,
        leader_name="HK",
        symbol=symbol,
        side=side,
        position_side=position_side,
        quantity=quantity,
        leverage=10,
        leader_notional=Decimal("100"),
        reason="test",
        leader_entry_price=leader_entry_price,
        **kwargs,
    )


class LateEntryConfigTests(unittest.TestCase):
    def test_negative_offset_rejected_when_enabled(self):
        with self.assertRaises(ValueError):
            LeaderConfig(
                "HK", "portfolio",
                late_entry_enabled=True, late_entry_offset_pct=-0.2,
            )

    def test_negative_offset_allowed_when_disabled(self):
        leader = LeaderConfig(
            "HK", "portfolio",
            late_entry_enabled=False, late_entry_offset_pct=-0.2,
        )
        self.assertEqual(leader.late_entry_offset_pct, -0.2)


class BitgetPlaceLimitOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_price_floored_to_price_place(self):
        client = BitgetClient("key", "secret", "passphrase")
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.001",
            "minOrderQty": "0.001",
            "pricePlace": "2",
        })
        client._request = AsyncMock(return_value={"orderId": "L1"})

        await client.place_limit_order(
            symbol="BTCUSDT", qty=Decimal("0.5"), side="buy",
            price=Decimal("99.839"), reduce_only=False, client_oid="copy-test",
        )

        body = client._request.await_args.kwargs["body"]
        self.assertEqual(body["price"], "99.83")
        self.assertEqual(body["orderType"], "limit")
        self.assertEqual(body["force"], "gtc")
        self.assertEqual(body["qty"], "0.500")

    async def test_limit_price_kept_when_price_place_absent(self):
        client = BitgetClient("key", "secret", "passphrase")
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.001",
            "minOrderQty": "0.001",
        })
        client._request = AsyncMock(return_value={"orderId": "L1"})

        await client.place_limit_order(
            symbol="BTCUSDT", qty=Decimal("0.5"), side="sell",
            price=Decimal("100.2"), reduce_only=False, client_oid="copy-test",
        )

        body = client._request.await_args.kwargs["body"]
        self.assertEqual(body["price"], "100.2")
        self.assertEqual(body["orderType"], "limit")


class BitgetExecutorLateEntryTests(unittest.IsolatedAsyncioTestCase):
    def _build(self, order_info):
        client = Mock()
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.001",
            "minOrderQty": "0.001",
        })
        client.format_quantity = Mock(return_value="0.5")
        client.set_leverage = AsyncMock(return_value=50)
        client.place_limit_order = AsyncMock(return_value={"orderId": "L1"})
        client.place_market_order = AsyncMock(return_value={"orderId": "M1"})
        client.get_order_info = AsyncMock(return_value=order_info)
        account = Mock()
        account.client = client
        return BitgetExecutor(account), client

    async def test_limit_unfilled_returns_pending(self):
        executor, client = self._build({
            "orderStatus": "new", "cumExecQty": "0", "avgPrice": "0", "orderId": "L1",
        })
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        with patch("src.bitget_executor.asyncio.sleep", new=AsyncMock()):
            result = await executor.execute(signal, Decimal("0.5"), limit_price=Decimal("100"))

        self.assertFalse(result.success)
        self.assertTrue(result.pending)
        self.assertEqual(result.order_id, "L1")
        self.assertEqual(result.avg_price, Decimal("100"))
        client.place_limit_order.assert_awaited_once()
        client.place_market_order.assert_not_awaited()

    async def test_limit_filled_returns_success(self):
        executor, client = self._build({
            "orderStatus": "filled", "cumExecQty": "0.5", "avgPrice": "99.8", "orderId": "L1",
        })
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        result = await executor.execute(signal, Decimal("0.5"), limit_price=Decimal("100"))

        self.assertTrue(result.success)
        self.assertFalse(result.pending)
        self.assertEqual(result.filled_qty, Decimal("0.5"))
        self.assertEqual(result.avg_price, Decimal("99.8"))
        self.assertEqual(result.order_id, "L1")
        client.place_limit_order.assert_awaited_once()
        client.place_market_order.assert_not_awaited()

    async def test_limit_goes_through_place_limit_order(self):
        executor, client = self._build({
            "orderStatus": "filled", "cumExecQty": "0.5", "avgPrice": "100", "orderId": "L1",
        })
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        await executor.execute(signal, Decimal("0.5"), limit_price=Decimal("100"))

        client.place_limit_order.assert_awaited_once()
        client.place_market_order.assert_not_awaited()
        call_kwargs = client.place_limit_order.await_args.kwargs
        self.assertEqual(call_kwargs["price"], Decimal("100"))
        self.assertEqual(call_kwargs["reduce_only"], False)

    async def test_market_goes_through_place_market_order(self):
        executor, client = self._build({
            "orderStatus": "filled", "cumExecQty": "0.5", "avgPrice": "100", "orderId": "M1",
        })
        signal = make_signal(SignalType.OPEN)

        result = await executor.execute(signal, Decimal("0.5"))

        self.assertTrue(result.success)
        client.place_market_order.assert_awaited_once()
        client.place_limit_order.assert_not_awaited()

    async def test_limit_details_unavailable_returns_pending_with_order_id(self):
        client = Mock()
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.001",
            "minOrderQty": "0.001",
        })
        client.format_quantity = Mock(return_value="0.5")
        client.set_leverage = AsyncMock(return_value=50)
        client.place_limit_order = AsyncMock(return_value={"orderId": "L1"})
        client.get_order_info = AsyncMock(side_effect=BitgetAPIError("boom"))
        account = Mock()
        account.client = client
        executor = BitgetExecutor(account)
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        with patch("src.bitget_executor.asyncio.sleep", new=AsyncMock()):
            result = await executor.execute(signal, Decimal("0.5"), limit_price=Decimal("100"))

        self.assertTrue(result.pending)
        self.assertEqual(result.order_id, "L1")
        self.assertEqual(result.avg_price, Decimal("100"))

    async def test_limit_partially_filled_returns_pending(self):
        executor, client = self._build({
            "orderStatus": "partially_filled", "cumExecQty": "0.2",
            "avgPrice": "99.9", "orderId": "L1",
        })
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        with patch("src.bitget_executor.asyncio.sleep", new=AsyncMock()):
            result = await executor.execute(signal, Decimal("0.5"), limit_price=Decimal("100"))

        self.assertFalse(result.success)
        self.assertTrue(result.pending)
        self.assertEqual(result.order_id, "L1")
        self.assertEqual(result.avg_price, Decimal("99.9"))


class CoordinatorLateEntryTests(unittest.IsolatedAsyncioTestCase):
    def _build_coordinator(self, *, late_entry_enabled=True, offset_pct=0.2):
        coordinator = Coordinator.__new__(Coordinator)
        coordinator._pending_late_orders = {}
        coordinator._unavailable_symbols = set()
        coordinator._order_cooldown = {}
        coordinator._order_cooldown_seconds = 180
        coordinator._skip_notice = set()
        coordinator._config = type("Config", (), {
            "leaders": [LeaderConfig(
                "HK", "portfolio",
                late_entry_enabled=late_entry_enabled,
                late_entry_offset_pct=offset_pct,
            )],
            "risk": RiskConfig(),
        })()
        coordinator._state = Mock()
        coordinator._state.mirror_positions = {}
        coordinator._state.get_mirror = Mock(return_value=None)
        coordinator._notifier = Mock()
        coordinator._notifier.notify_skipped = AsyncMock()
        coordinator._notifier.notify_trade = AsyncMock()
        coordinator._notifier.notify_error = AsyncMock()
        coordinator._notifier.send = AsyncMock()
        coordinator._sizer = Mock()
        coordinator._sizer.calculate = Mock(return_value=Decimal("0.5"))

        client = Mock()
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.01",
            "minOrderQty": "0.01",
            "minNotionalValue": "5",
            "pricePlace": "2",
        })
        client.get_ticker = AsyncMock(return_value={"markPrice": "100", "lastPrice": "100"})
        client.get_order_info = AsyncMock(return_value={
            "orderStatus": "new", "cumExecQty": "0", "orderId": "L1",
        })
        account = Mock()
        account.client = client
        account.get_positions = AsyncMock(return_value=[])
        account.get_total_margin = AsyncMock(return_value=Decimal("10000"))
        coordinator._account = account

        coordinator._executor = Mock()
        coordinator._executor.execute = AsyncMock()
        return coordinator

    async def test_open_buy_limit_below_leader_entry(self):
        coordinator = self._build_coordinator()
        coordinator._executor.execute.return_value = OrderResult(
            success=False, order_id="L1", avg_price=Decimal("99.8"),
            pending=True, error="Limit order pending",
        )
        signal = make_signal(SignalType.OPEN, side="buy", leader_entry_price=Decimal("100"))

        await coordinator._handle_signal(signal)

        self.assertEqual(
            coordinator._executor.execute.await_args.kwargs["limit_price"],
            Decimal("99.8"),
        )
        self.assertEqual(
            coordinator._pending_late_orders, {("HK", "BTCUSDT", "BOTH"): "L1"},
        )
        coordinator._notifier.notify_skipped.assert_called_once()

    async def test_open_sell_limit_above_leader_entry(self):
        coordinator = self._build_coordinator()
        coordinator._executor.execute.return_value = OrderResult(
            success=False, order_id="L1", avg_price=Decimal("100.2"),
            pending=True, error="Limit order pending",
        )
        signal = make_signal(SignalType.OPEN, side="sell", leader_entry_price=Decimal("100"))

        await coordinator._handle_signal(signal)

        self.assertEqual(
            coordinator._executor.execute.await_args.kwargs["limit_price"],
            Decimal("100.2"),
        )

    async def test_open_limit_falls_back_to_mark_price(self):
        coordinator = self._build_coordinator()
        coordinator._executor.execute.return_value = OrderResult(
            success=False, order_id="L1", avg_price=Decimal("99.8"),
            pending=True, error="Limit order pending",
        )
        signal = make_signal(SignalType.OPEN, side="buy", leader_entry_price=None)

        await coordinator._handle_signal(signal)

        self.assertEqual(
            coordinator._executor.execute.await_args.kwargs["limit_price"],
            Decimal("99.8"),
        )

    async def test_late_entry_disabled_uses_market_order(self):
        coordinator = self._build_coordinator(late_entry_enabled=False)
        coordinator._executor.execute.return_value = OrderResult(
            success=True, order_id="M1", filled_qty=Decimal("0.5"), avg_price=Decimal("100"),
        )
        signal = make_signal(SignalType.OPEN, leader_entry_price=Decimal("100"))

        await coordinator._handle_signal(signal)

        self.assertIsNone(coordinator._executor.execute.await_args.kwargs["limit_price"])
        self.assertEqual(coordinator._pending_late_orders, {})

    async def test_non_open_signals_never_use_limit_price(self):
        coordinator = self._build_coordinator()
        coordinator._executor.execute.return_value = OrderResult(
            success=True, order_id="M1", filled_qty=Decimal("0.5"), avg_price=Decimal("100"),
        )
        signal = make_signal(SignalType.INCREASE, leader_entry_price=Decimal("100"))

        await coordinator._handle_signal(signal)

        self.assertIsNone(coordinator._executor.execute.await_args.kwargs["limit_price"])

    async def test_pending_order_cancels_on_increase_decrease_or_close(self):
        coordinator = self._build_coordinator()

        for signal_type in (SignalType.INCREASE, SignalType.DECREASE, SignalType.CLOSE):
            with self.subTest(signal_type=signal_type):
                coordinator._pending_late_orders = {("HK", "BTCUSDT", "BOTH"): "L1"}
                coordinator._account.client.cancel_order = AsyncMock()
                signal = make_signal(signal_type)
                await coordinator._handle_signal(signal)
                coordinator._executor.execute.assert_not_awaited()
                coordinator._account.client.cancel_order.assert_awaited_once_with(
                    symbol="BTCUSDT", order_id="L1",
                )
                self.assertEqual(coordinator._pending_late_orders, {})

    async def test_filled_pending_order_cleared_and_signal_continues(self):
        coordinator = self._build_coordinator()
        coordinator._pending_late_orders = {("HK", "BTCUSDT", "BOTH"): "L1"}
        coordinator._account.client.get_order_info = AsyncMock(return_value={
            "orderStatus": "filled", "cumExecQty": "0.5", "orderId": "L1",
        })
        coordinator._account.get_positions = AsyncMock(return_value=[
            {"contract": "BTCUSDT", "size": "0.5"},
        ])
        coordinator._executor.execute.return_value = OrderResult(
            success=True, order_id="M1", filled_qty=Decimal("0.5"), avg_price=Decimal("100"),
        )

        await coordinator._handle_signal(make_signal(SignalType.INCREASE))

        self.assertEqual(coordinator._pending_late_orders, {})
        coordinator._executor.execute.assert_awaited_once()
        self.assertIsNone(coordinator._executor.execute.await_args.kwargs["limit_price"])

    async def test_partially_filled_pending_order_keeps_skipping(self):
        coordinator = self._build_coordinator()
        coordinator._pending_late_orders = {("HK", "BTCUSDT", "BOTH"): "L1"}
        coordinator._account.client.cancel_order = AsyncMock()
        coordinator._account.client.get_order_info = AsyncMock(return_value={
            "orderStatus": "partially_filled", "cumExecQty": "0.1", "orderId": "L1",
        })

        await coordinator._handle_signal(make_signal(SignalType.INCREASE))

        # Cancel the remaining quantity before skipping the signal, so it
        # cannot later fill after the leader has changed the position.
        coordinator._account.client.cancel_order.assert_awaited_once_with(
            symbol="BTCUSDT", order_id="L1",
        )
        self.assertEqual(coordinator._pending_late_orders, {})
        coordinator._executor.execute.assert_not_awaited()

    async def test_cancelled_pending_order_cleared_and_signal_continues(self):
        coordinator = self._build_coordinator()
        coordinator._pending_late_orders = {("HK", "BTCUSDT", "BOTH"): "L1"}
        coordinator._account.client.get_order_info = AsyncMock(return_value={
            "orderStatus": "cancelled", "cumExecQty": "0", "orderId": "L1",
        })
        coordinator._account.get_positions = AsyncMock(return_value=[
            {"contract": "BTCUSDT", "size": "0.5"},
        ])
        coordinator._executor.execute.return_value = OrderResult(
            success=True, order_id="M1", filled_qty=Decimal("0.5"), avg_price=Decimal("100"),
        )

        await coordinator._handle_signal(make_signal(SignalType.CLOSE))

        self.assertEqual(coordinator._pending_late_orders, {})
        coordinator._executor.execute.assert_awaited_once()
        self.assertIsNone(coordinator._executor.execute.await_args.kwargs["limit_price"])


if __name__ == "__main__":
    unittest.main()
