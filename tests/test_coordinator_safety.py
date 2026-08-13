import unittest

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.coordinator import Coordinator
from src.detector import detect_changes
from src.config import LeaderConfig, RiskConfig
from src.models import LeaderPosition, LeaderState, OrderResult, SignalType, TradeSignal
from src.sizer import Sizer
from src.state import State


class LeaderStateTests(unittest.TestCase):
    def test_leader_state_starts_with_no_positions(self):
        state = LeaderState(
            leader_name="ANS",
            portfolio_id="portfolio",
        )

        self.assertEqual(state.positions, {})
        self.assertEqual(state.consecutive_errors, 0)

    def test_successful_empty_snapshot_generates_close_signal(self):
        position = LeaderPosition(
            symbol="BTCUSDT",
            position_side="BOTH",
            position_amount=Decimal("1"),
            entry_price=Decimal("60000"),
            leverage=10,
            notional_value=Decimal("60000"),
            mark_price=Decimal("60000"),
            isolated=False,
        )

        signals = detect_changes("ANS", {position.key: position}, [])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type.value, "close")
        self.assertEqual(signals[0].side, "sell")
        self.assertEqual(signals[0].quantity, Decimal("1"))

    def test_decrease_signal_keeps_leader_old_and_new_quantity(self):
        old = LeaderPosition(
            symbol="BLESSUSDT", position_side="BOTH", position_amount=Decimal("100"),
            entry_price=Decimal("1"), leverage=10, notional_value=Decimal("100"),
            mark_price=Decimal("1"), isolated=False,
        )
        new = LeaderPosition(
            symbol="BLESSUSDT", position_side="BOTH", position_amount=Decimal("60"),
            entry_price=Decimal("1"), leverage=10, notional_value=Decimal("60"),
            mark_price=Decimal("1"), isolated=False,
        )

        signal = detect_changes("HK", {old.key: old}, [new])[0]

        self.assertEqual(signal.leader_old_quantity, Decimal("100"))
        self.assertEqual(signal.leader_new_quantity, Decimal("60"))

    def test_decrease_can_size_without_mirror_from_live_quantity(self):
        sizer = Sizer(RiskConfig())
        signal = detect_changes("HK", {
            ("BLESSUSDT", "BOTH"): LeaderPosition(
                symbol="BLESSUSDT", position_side="BOTH", position_amount=Decimal("100"),
                entry_price=Decimal("1"), leverage=10, notional_value=Decimal("100"),
                mark_price=Decimal("1"), isolated=False,
            )
        }, [LeaderPosition(
            symbol="BLESSUSDT", position_side="BOTH", position_amount=Decimal("60"),
            entry_price=Decimal("1"), leverage=10, notional_value=Decimal("60"),
            mark_price=Decimal("1"), isolated=False,
        )])[0]

        quantity = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio"),
            my_margin=Decimal("100"),
            my_mirror=None,
            mark_price=Decimal("1"),
            current_quantity=Decimal("50"),
        )

        self.assertEqual(quantity, Decimal("20"))

    def test_decrease_caps_to_base_qty_without_mirror(self):
        sizer = Sizer(RiskConfig())
        signal = TradeSignal(
            signal_type=SignalType.DECREASE,
            leader_name="HK",
            symbol="BLESSUSDT",
            side="sell",
            position_side="BOTH",
            quantity=Decimal("1.5"),
            leverage=10,
            leader_notional=Decimal("100"),
            reason="test",
            leader_old_quantity=Decimal("1"),
        )

        quantity = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio"),
            my_margin=Decimal("100"),
            my_mirror=None,
            mark_price=Decimal("1"),
            current_quantity=Decimal("1"),
        )

        self.assertEqual(quantity, Decimal("1.0"))

    def test_open_uses_fixed_balance_instead_of_live_margin(self):
        sizer = Sizer(RiskConfig(fixed_balance_usdt=Decimal("1000")))
        signal = TradeSignal(
            signal_type=SignalType.OPEN,
            leader_name="HK",
            symbol="BTCUSDT",
            side="buy",
            position_side="BOTH",
            quantity=Decimal("1"),
            leverage=10,
            leader_notional=Decimal("5000"),
            reason="test",
        )

        quantity = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio", coefficient=1.0, total_margin=10000),
            my_margin=Decimal("100"),
            my_mirror=None,
            mark_price=Decimal("100"),
        )

        # 5000 / 10000 * fixed 1000 / price 100 = 5 contracts.
        self.assertEqual(quantity, Decimal("5.00000000"))


class OrderValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.coordinator = Coordinator.__new__(Coordinator)
        account = type("Account", (), {})()
        account.client = type("Client", (), {})()
        account.client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.01",
            "minOrderQty": "0.01",
            "minNotionalValue": "5",
        })
        self.coordinator._account = account

        # Coordinator.__new__ 跳过 __init__，这里补齐 _handle_signal 流程会用到的属性
        self.coordinator._config = type("Config", (), {
            "leaders": [],
            "risk": RiskConfig(),
        })()
        self.coordinator._pending_late_orders = {}
        self.coordinator._unavailable_symbols = set()
        self.coordinator._order_cooldown = {}
        self.coordinator._order_cooldown_seconds = 180
        self.coordinator._skip_notice = set()
        self.coordinator._state = State()
        self.coordinator._sizer = MagicMock()
        self.coordinator._executor = MagicMock()
        self.coordinator._notifier = MagicMock()
        for method in ("send", "notify_error", "notify_skipped", "notify_trade"):
            setattr(self.coordinator._notifier, method, AsyncMock())

    async def test_leader_symbol_blacklist_skips_all_signal_types(self):
        self.coordinator._config.leaders = [
            LeaderConfig("HK", "portfolio", symbol_blacklist=["btcusdt"])
        ]

        for signal_type in SignalType:
            signal = TradeSignal(
                signal_type=signal_type,
                leader_name="HK",
                symbol="BTCUSDT",
                side="buy",
                position_side="BOTH",
                quantity=Decimal("1"),
                leverage=10,
                leader_notional=Decimal("100"),
                reason="test",
            )
            with self.subTest(signal_type=signal_type):
                await self.coordinator._handle_signal(signal)
            # 命中黑名单应安静跳过：不通知、不下单
            self.coordinator._notifier.notify_skipped.assert_not_called()
            self.coordinator._notifier.notify_trade.assert_not_called()
            self.coordinator._executor.execute.assert_not_called()

    async def test_symbol_blacklist_is_case_insensitive(self):
        self.coordinator._config.leaders = [
            LeaderConfig("HK", "portfolio", symbol_blacklist=["bTcUsDt"])
        ]
        signal = TradeSignal(
            signal_type=SignalType.OPEN,
            leader_name="HK",
            symbol="btcusdt",  # 信号侧全小写也应命中
            side="buy",
            position_side="BOTH",
            quantity=Decimal("1"),
            leverage=10,
            leader_notional=Decimal("100"),
            reason="test",
        )

        await self.coordinator._handle_signal(signal)

        self.coordinator._executor.execute.assert_not_called()
        self.coordinator._notifier.notify_trade.assert_not_called()

    async def test_symbol_blacklist_is_isolated_per_leader(self):
        # leader A 黑名单含 BTCUSDT，leader B 无黑名单
        self.coordinator._config.leaders = [
            LeaderConfig("A", "portfolio", symbol_blacklist=["BTCUSDT"]),
            LeaderConfig("B", "portfolio"),
        ]
        self.coordinator._account.get_total_margin = AsyncMock(
            return_value=Decimal("1000")
        )
        self.coordinator._account.get_positions = AsyncMock(return_value=[])
        self.coordinator._account.client.get_ticker = AsyncMock(
            return_value={"markPrice": "50000"}
        )
        self.coordinator._sizer.calculate = MagicMock(return_value=Decimal("1"))
        self.coordinator._executor.execute = AsyncMock(return_value=OrderResult(
            success=True,
            order_id="123",
            filled_qty=Decimal("1"),
            avg_price=Decimal("50000"),
        ))

        signal = TradeSignal(
            signal_type=SignalType.OPEN,
            leader_name="B",
            symbol="BTCUSDT",
            side="buy",
            position_side="BOTH",
            quantity=Decimal("1"),
            leverage=10,
            leader_notional=Decimal("5000"),
            reason="test",
        )

        await self.coordinator._handle_signal(signal)

        # B 的信号不被 A 的黑名单拦截，流程应走到 executor 与成交通知
        self.coordinator._executor.execute.assert_awaited_once()
        self.coordinator._notifier.notify_trade.assert_awaited_once()

    async def test_symbol_blacklist_ignores_whitespace(self):
        # __post_init__ 只做大写归一化，coordinator 侧再容忍前后空格
        self.coordinator._config.leaders = [
            LeaderConfig("HK", "portfolio", symbol_blacklist=[" btcusdt "])
        ]
        signal = TradeSignal(
            signal_type=SignalType.OPEN,
            leader_name="HK",
            symbol="BTCUSDT",
            side="buy",
            position_side="BOTH",
            quantity=Decimal("1"),
            leverage=10,
            leader_notional=Decimal("100"),
            reason="test",
        )

        await self.coordinator._handle_signal(signal)

        self.coordinator._executor.execute.assert_not_called()

    async def test_valid_quantity_rounds_down_to_step(self):
        rounded, error = await self.coordinator._validate_order_size(
            "BTCUSDT", Decimal("1.234"), Decimal("100"),
        )
        self.assertEqual(rounded, Decimal("1.23"))
        self.assertIsNone(error)

    async def test_quantity_below_step_is_rejected(self):
        rounded, error = await self.coordinator._validate_order_size(
            "BTCUSDT", Decimal("0.005"), Decimal("100"),
        )
        self.assertEqual(rounded, Decimal("0"))
        self.assertIsNotNone(error)

    async def test_small_notional_is_rejected(self):
        rounded, error = await self.coordinator._validate_order_size(
            "BTCUSDT", Decimal("0.5"), Decimal("1"),
        )
        self.assertEqual(rounded, Decimal("0.5"))
        self.assertIn("notional", error)


if __name__ == "__main__":
    unittest.main()
