import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from src.config import LeaderConfig, RiskConfig
from src.coordinator import Coordinator
from src.models import OrderResult, SignalType, TradeSignal
from src.sizer import Sizer


class FixedBalanceSizerTests(unittest.TestCase):
    """固定余额模式下 Sizer 的 OPEN / INCREASE / DECREASE 行为。"""

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
        # 5000 / 10000 * 固定余额 1000 / 价格 100 = 5，实时余额 100 被忽略
        self.assertEqual(quantity, Decimal("5.00000000"))

        # 实时余额再大也不影响结果
        quantity2 = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio", coefficient=1.0, total_margin=10000),
            my_margin=Decimal("999999"),
            my_mirror=None,
            mark_price=Decimal("100"),
        )
        self.assertEqual(quantity2, Decimal("5.00000000"))

    def test_none_fixed_balance_uses_live_margin(self):
        # 默认 None：仍使用实时账户余额
        sizer = Sizer(RiskConfig())
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
        # 5000 / 10000 * 实时余额 100 / 价格 100 = 0.5
        self.assertEqual(quantity, Decimal("0.50000000"))

    def test_increase_target_and_delta_use_fixed_balance(self):
        sizer = Sizer(RiskConfig(fixed_balance_usdt=Decimal("1000")))
        signal = TradeSignal(
            signal_type=SignalType.INCREASE,
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
            current_quantity=Decimal("2"),
        )
        # 目标名义 = 1.0 * 5000 / 10000 * 固定余额 1000 = 500
        # 当前名义 = 2 * 100 = 200，增量名义 = 300，增量数量 = 300 / 100 = 3
        self.assertEqual(quantity, Decimal("3.00000000"))

    def test_increase_without_fixed_balance_uses_live_margin_target(self):
        # 无固定余额时按实时余额 100 算目标名义 = 50，小于当前 200，应跳过
        sizer = Sizer(RiskConfig())
        signal = TradeSignal(
            signal_type=SignalType.INCREASE,
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
            current_quantity=Decimal("2"),
        )
        self.assertIsNone(quantity)

    def test_decrease_unaffected_by_fixed_balance(self):
        sizer = Sizer(RiskConfig(fixed_balance_usdt=Decimal("1000")))
        signal = TradeSignal(
            signal_type=SignalType.DECREASE,
            leader_name="HK",
            symbol="BLESSUSDT",
            side="sell",
            position_side="BOTH",
            quantity=Decimal("40"),
            leverage=10,
            leader_notional=Decimal("100"),
            reason="test",
            leader_old_quantity=Decimal("100"),
        )

        quantity = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio"),
            my_margin=Decimal("1"),
            my_mirror=None,
            mark_price=Decimal("1"),
            current_quantity=Decimal("50"),
        )
        # 按比例减仓：50 * (40 / 100) = 20，与固定余额无关
        self.assertEqual(quantity, Decimal("20.00000000"))

    def test_decrease_ignores_invalid_fixed_balance(self):
        # 固定余额为 0（非法值）时，DECREASE 仍在固定余额校验前返回，
        # 不触发 ValueError，仍按 current_quantity / leader_old_quantity 比例减仓
        sizer = Sizer(RiskConfig(fixed_balance_usdt=Decimal("0")))
        signal = TradeSignal(
            signal_type=SignalType.DECREASE,
            leader_name="HK",
            symbol="BLESSUSDT",
            side="sell",
            position_side="BOTH",
            quantity=Decimal("40"),
            leverage=10,
            leader_notional=Decimal("100"),
            reason="test",
            leader_old_quantity=Decimal("100"),
        )

        quantity = sizer.calculate(
            signal=signal,
            leader_cfg=LeaderConfig("HK", "portfolio"),
            my_margin=Decimal("100"),
            my_mirror=None,
            mark_price=Decimal("1"),
            current_quantity=Decimal("50"),
        )
        self.assertEqual(quantity, Decimal("20.00000000"))

    def test_fixed_balance_zero_or_negative_raises_value_error(self):
        for bad_balance in (Decimal("0"), Decimal("-100")):
            sizer = Sizer(RiskConfig(fixed_balance_usdt=bad_balance))
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
            with self.assertRaises(ValueError):
                sizer.calculate(
                    signal=signal,
                    leader_cfg=LeaderConfig("HK", "portfolio", coefficient=1.0, total_margin=10000),
                    my_margin=Decimal("100"),
                    my_mirror=None,
                    mark_price=Decimal("100"),
                )


class ClosePathFixedBalanceTests(unittest.IsolatedAsyncioTestCase):
    """CLOSE 由 coordinator 用实盘持仓数量全平，不经过 Sizer，固定余额不影响。"""

    async def asyncSetUp(self):
        self.coordinator = Coordinator.__new__(Coordinator)
        self.coordinator._pending_late_orders = {}
        self.coordinator._unavailable_symbols = set()
        self.coordinator._order_cooldown = {}
        # 固定余额配成非法值：一旦 Sizer 被调用必然抛 ValueError
        self.coordinator._config = type("Config", (), {
            "leaders": [LeaderConfig("HK", "portfolio")],
            "risk": RiskConfig(fixed_balance_usdt=Decimal("0")),
        })
        state = type("State", (), {})()
        state.mirror_positions = {}
        state.get_mirror = lambda *args: None
        state.remove_mirror = lambda *args: None
        self.coordinator._state = state

        account = type("Account", (), {})()
        account.get_positions = AsyncMock(return_value=[
            {"contract": "BTCUSDT", "size": "0.5", "entry_price": "100"},
        ])
        self.coordinator._account = account

        self.coordinator._notifier = type("Notifier", (), {
            "send": AsyncMock(),
            "notify_skipped": AsyncMock(),
            "notify_error": AsyncMock(),
            "notify_trade": AsyncMock(),
        })()

        # calculate 一旦被调用立即失败，证明 CLOSE 完全不经过 Sizer
        sizer = Sizer(RiskConfig(fixed_balance_usdt=Decimal("0")))
        sizer.calculate = AsyncMock(
            side_effect=AssertionError("Sizer must not be called for CLOSE")
        )
        self.coordinator._sizer = sizer

        self.executor = type("Executor", (), {})()
        self.executor.execute = AsyncMock(return_value=OrderResult(
            success=True,
            order_id="close-1",
            filled_qty=Decimal("0.5"),
            avg_price=Decimal("110"),
        ))
        self.coordinator._executor = self.executor

    async def test_close_bypasses_sizer_and_uses_live_position_quantity(self):
        signal = TradeSignal(
            signal_type=SignalType.CLOSE,
            leader_name="HK",
            symbol="BTCUSDT",
            side="sell",
            position_side="BOTH",
            quantity=Decimal("1"),  # 与实盘持仓不同，证明 CLOSE 用的是实盘数量
            leverage=10,
            leader_notional=Decimal("5000"),
            reason="leader closed",
        )

        await self.coordinator._handle_signal(signal)

        self.coordinator._sizer.calculate.assert_not_called()
        self.assertEqual(self.executor.execute.call_count, 1)
        # 下单数量 = 实盘持仓 0.5，而不是信号里的 1
        args = self.executor.execute.call_args
        self.assertEqual(args.args[1], Decimal("0.5"))
        self.assertEqual(self.coordinator._notifier.notify_trade.call_count, 1)


if __name__ == "__main__":
    unittest.main()
