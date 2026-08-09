import unittest
from decimal import Decimal

from src.position_source import PollError, parse_leader_positions


class PositionSourceParsingTests(unittest.TestCase):
    def test_filters_all_numeric_zero_representations(self):
        items = [
            self._position("0"),
            self._position("0.0"),
            self._position("0.000"),
            self._position("-1.25"),
        ]

        positions = parse_leader_positions(items, "leader-1")

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].position_amount, Decimal("-1.25"))

    def test_rejects_non_list_payload(self):
        with self.assertRaises(PollError):
            parse_leader_positions({}, "leader-1")

    @staticmethod
    def _position(amount):
        return {
            "symbol": "BTCUSDT",
            "positionSide": "BOTH",
            "positionAmount": amount,
            "entryPrice": "60000",
            "leverage": "10",
            "notionalValue": "75000",
            "markPrice": "60000",
            "isolated": False,
        }


if __name__ == "__main__":
    unittest.main()
