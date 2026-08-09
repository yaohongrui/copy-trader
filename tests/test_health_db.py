import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.health_db import HealthDatabase


class HealthDatabaseTests(unittest.TestCase):
    def test_records_hourly_balance_with_decimal_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.db"
            database = HealthDatabase(path)
            checked_at = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)

            database.record_balance(checked_at, Decimal("222.79507696"))
            database.record_balance(checked_at, Decimal("223.10"))

            import sqlite3
            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    "SELECT checked_at, balance_usdt FROM hourly_balance"
                ).fetchall()
            hourly = checked_at.replace(minute=0, second=0, microsecond=0)
            self.assertEqual(rows, [(hourly.isoformat(), "223.10")])
            self.assertEqual(database.fetch_balances(), [(hourly.isoformat(), "223.10")])


if __name__ == "__main__":
    unittest.main()
