import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.state import State


class StatePendingLateOrderTests(unittest.TestCase):
    def test_pending_late_orders_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with patch("src.state.STATE_FILE", state_path):
                state = State()
                state.pending_late_orders[("HK", "BTCUSDT", "BOTH")] = "order-123"
                state.save()

                self.assertEqual(
                    json.loads(state_path.read_text())["pending_late_orders"],
                    {"HK|BTCUSDT|BOTH": "order-123"},
                )

                restored = State()
                restored.load()
                self.assertEqual(
                    restored.pending_late_orders,
                    {("HK", "BTCUSDT", "BOTH"): "order-123"},
                )


if __name__ == "__main__":
    unittest.main()
