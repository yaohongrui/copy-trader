"""Contracts and shared parsing for Binance leader-position sources.

Position data may be obtained with direct HTTP headers or from an authenticated
browser.  The coordinator only consumes this module's common contract, so the
authentication method does not leak into trading logic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from src.models import LeaderPosition


class PollError(Exception):
    """Position source failed due to transport or response-format problems."""


class PollAuthError(PollError):
    """The position source no longer has a usable Binance session."""


class PositionSource(Protocol):
    """Lifecycle and read interface required by the coordinator."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def fetch_positions(self, portfolio_id: str) -> list[LeaderPosition]: ...


def parse_leader_positions(items: Any, portfolio_id: str) -> list[LeaderPosition]:
    """Convert a successful Binance ``data`` list into domain positions.

    A zero amount is intentionally excluded.  Binance represents zeros with
    several strings (for example ``0``, ``0.0`` and ``0.000``), so numeric
    Decimal comparison is required.
    """
    if not isinstance(items, list):
        raise PollError(f"Unexpected positions format for {portfolio_id}")

    try:
        positions = []
        for item in items:
            amount = Decimal(str(item["positionAmount"]))
            if amount == 0:
                continue
            positions.append(LeaderPosition(
                symbol=item["symbol"],
                position_side=item["positionSide"],
                position_amount=amount,
                entry_price=Decimal(str(item["entryPrice"])),
                leverage=int(item["leverage"]),
                notional_value=Decimal(str(item["notionalValue"])),
                mark_price=Decimal(str(item["markPrice"])),
                isolated=item["isolated"],
            ))
        return positions
    except Exception as exc:
        raise PollError(f"Failed to parse positions for {portfolio_id}: {exc}") from exc
