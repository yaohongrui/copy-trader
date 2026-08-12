import asyncio
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from src.coordinator import Coordinator
from src.models import MirrorPosition, SignalType, TradeSignal


class CoordinatorFormattingTests(unittest.TestCase):
    def test_account_position_formats_entry_and_pnl_to_two_decimals(self):
        coordinator = Coordinator.__new__(Coordinator)

        self.assertEqual(
            coordinator._format_account_position({
                "contract": "BTCUSDT",
                "size": "0.0069",
                "entry_price": "63303.153692201519",
                "mark_price": "63656.6",
                "unrealized_pnl": "2.43877952",
                "roi_pct": "26.95",
            }),
            "BTCUSDT qty=0.0069 entry=63303.15 mark=63656.6 "
            "unrealized_pnl=2.44 ROI=26.95%",
        )

    def test_account_position_keeps_unknown_entry_value(self):
        coordinator = Coordinator.__new__(Coordinator)

        self.assertIn(
            "entry=? mark=? unrealized_pnl=0.00",
            coordinator._format_account_position({"entry_price": "?"}),
        )

    def test_actual_position_quantity_comes_from_exchange(self):
        coordinator = Coordinator.__new__(Coordinator)
        coordinator._account = type("Account", (), {})()
        coordinator._account.get_positions = AsyncMock(return_value=[
            {"contract": "SOXLUSDT", "size": "-3.19"},
        ])

        quantity = asyncio.run(
            coordinator._get_actual_position_quantity("SOXLUSDT")
        )

        self.assertEqual(quantity, Decimal("3.19"))

    def test_full_close_removes_all_mirrors_for_symbol(self):
        coordinator = Coordinator.__new__(Coordinator)
        coordinator._state = type("State", (), {})()
        coordinator._state.mirror_positions = {}
        coordinator._state.set_mirror = lambda pos: coordinator._state.mirror_positions.__setitem__(
            (pos.leader_name, pos.symbol, pos.position_side), pos
        )
        coordinator._state.set_mirror(MirrorPosition(
            "HK", "SOXLUSDT", "BOTH", Decimal("3.19"), Decimal("100"),
        ))
        coordinator._state.set_mirror(MirrorPosition(
            "other", "SOXLUSDT", "BOTH", Decimal("1"), Decimal("10"),
        ))
        coordinator._state.set_mirror(MirrorPosition(
            "HK", "MUUSDT", "BOTH", Decimal("0.07"), Decimal("20"),
        ))

        coordinator._update_state_after_trade(TradeSignal(
            signal_type=SignalType.CLOSE,
            leader_name="HK",
            symbol="SOXLUSDT",
            side="buy",
            position_side="BOTH",
            quantity=Decimal("3.19"),
            leverage=20,
            leader_notional=Decimal("100"),
            reason="leader closed",
        ), Decimal("3.19"))

        self.assertFalse(any(pos.symbol == "SOXLUSDT"
                             for pos in coordinator._state.mirror_positions.values()))
        self.assertIn(("HK", "MUUSDT", "BOTH"), coordinator._state.mirror_positions)

    def test_prune_poll_tasks_removes_finished_tasks(self):
        async def scenario():
            coordinator = Coordinator.__new__(Coordinator)

            async def noop():
                return None

            coordinator._poll_tasks = {
                "finished": asyncio.get_running_loop().create_task(noop()),
            }
            await asyncio.sleep(0)
            coordinator._prune_poll_tasks()
            return dict(coordinator._poll_tasks)

        self.assertEqual(asyncio.run(scenario()), {})


if __name__ == "__main__":
    unittest.main()
