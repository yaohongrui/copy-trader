from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from time import time


class SignalType(Enum):
    OPEN = "open"
    CLOSE = "close"
    INCREASE = "increase"
    DECREASE = "decrease"


@dataclass
class LeaderPosition:
    symbol: str
    position_side: str
    position_amount: Decimal
    entry_price: Decimal
    leverage: int
    notional_value: Decimal
    mark_price: Decimal
    isolated: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.position_side)

    @property
    def is_long(self) -> bool:
        return self.position_amount > 0


@dataclass
class TradeSignal:
    signal_type: SignalType
    leader_name: str
    symbol: str
    side: str  # buy/sell (trading direction)
    position_side: str  # LONG/SHORT/BOTH (position direction)
    quantity: Decimal
    leverage: int
    leader_notional: Decimal
    reason: str
    # Snapshot quantities let sizing recover when the local mirror is absent.
    leader_old_quantity: Decimal | None = None
    leader_new_quantity: Decimal | None = None


@dataclass
class MirrorPosition:
    leader_name: str
    symbol: str
    position_side: str
    our_quantity: Decimal
    leader_quantity_at_sync: Decimal
    opened_at: float = field(default_factory=time)


@dataclass
class LeaderState:
    leader_name: str
    portfolio_id: str
    positions: dict[tuple[str, str], LeaderPosition] = field(default_factory=dict)
    last_poll_time: float = 0.0
    consecutive_errors: int = 0
    initialized: bool = False  # 本次进程是否已经完成首轮仓位同步


@dataclass
class OrderResult:
    success: bool
    order_id: str | None = None
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)
    error: str | None = None
