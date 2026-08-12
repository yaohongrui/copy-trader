import asyncio
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from src.coordinator import Coordinator
from src.health import HealthReporter
from src.models import MirrorPosition, SignalType, TradeSignal
from src.notifier import Notifier


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
            "BTCUSDT | LONG | 0.0069 | 63303.15 | 63656.6 | 2.44 | 26.95%",
        )

    def test_account_position_keeps_unknown_entry_value(self):
        coordinator = Coordinator.__new__(Coordinator)

        self.assertIn(
            "? | ? | 0 | ? | ? | 0.00 | 0.00%",
            coordinator._format_account_position({"entry_price": "?"}),
        )

    def test_trade_pnl_uses_entry_price_and_position_direction(self):
        coordinator = Coordinator.__new__(Coordinator)
        signal = TradeSignal(
            signal_type=SignalType.CLOSE, leader_name="leader", symbol="BTCUSDT",
            side="sell", position_side="BOTH", quantity=Decimal("1"), leverage=1,
            leader_notional=Decimal("0"), reason="test",
        )
        label, pnl = asyncio.run(coordinator._get_trade_pnl(
            signal, Decimal("110"), Decimal("2"),
            {"entry_price": "100", "size": "3"},
        ))

        self.assertEqual(label, "Trade PnL (pre-fee)")
        self.assertEqual(pnl, "+20.00 USDT")

    def test_trade_notification_uses_action_emoji_and_hides_order_id(self):
        notifier = Notifier.__new__(Notifier)
        messages = []

        async def send(message):
            messages.append(message)

        notifier.send = send
        asyncio.run(notifier.notify_trade(
            leader="alpha", symbol="BTCUSDT", side="sell", qty="0.01",
            signal_type="decrease", avg_price="100000", pnl_label="Trade PnL (pre-fee)",
            pnl="+12.34 USDT",
        ))

        self.assertEqual(len(messages), 1)
        self.assertIn("➖ <b>DECREASE FILLED</b>", messages[0])
        self.assertIn("Trade PnL (pre-fee)", messages[0])
        self.assertNotIn("Order ID", messages[0])

    def test_health_table_aligns_mixed_width_leader_names(self):
        table = HealthReporter._format_table("HK: API OK\n赌怪: API OK")

        self.assertEqual(table, "<pre>HK    API OK\n赌怪  API OK</pre>")

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
