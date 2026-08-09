"""Small SQLite store for hourly account-balance snapshots."""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path


class HealthDatabase:
    """Persist one balance snapshot for each hourly health check."""

    def __init__(self, path: str | Path = "data/health.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hourly_balance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL UNIQUE,
                    balance_usdt TEXT NOT NULL
                )
                """
            )

    def record_balance(self, checked_at: datetime, balance: Decimal) -> None:
        """Insert or replace a snapshot, keeping Decimal precision as text."""
        hourly = checked_at.replace(minute=0, second=0, microsecond=0)
        timestamp = hourly.isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hourly_balance (checked_at, balance_usdt)
                VALUES (?, ?)
                ON CONFLICT(checked_at) DO UPDATE SET balance_usdt=excluded.balance_usdt
                """,
                (timestamp, str(balance)),
            )

    def fetch_balances(self, limit: int | None = None) -> list[tuple[str, str]]:
        """Return ``(checked_at, balance_usdt)`` rows, oldest first."""
        query = "SELECT checked_at, balance_usdt FROM hourly_balance ORDER BY checked_at"
        params: tuple[int, ...] = ()
        if limit is not None:
            query = (
                "SELECT checked_at, balance_usdt FROM (" + query +
                " DESC LIMIT ?) ORDER BY checked_at"
            )
            params = (limit,)
        with self._connect() as connection:
            return [(row[0], row[1]) for row in connection.execute(query, params)]

    def close(self) -> None:
        """Compatibility hook; connections are short-lived per operation."""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
