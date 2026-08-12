import unittest

from decimal import Decimal
from unittest.mock import AsyncMock

from src.coordinator import Coordinator
from src.detector import detect_changes
from src.config import LeaderConfig, RiskConfig
from src.models import LeaderPosition, LeaderState, SignalType, TradeSignal
from src.sizer import Sizer


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
