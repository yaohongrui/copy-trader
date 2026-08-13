import json
import logging
from decimal import Decimal
from pathlib import Path
from time import time

from src.models import LeaderState, MirrorPosition

logger = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".copy-trader" / "state.json"


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


class State:
    def __init__(self):
        self.leader_states: dict[str, LeaderState] = {}
        self.mirror_positions: dict[tuple[str, str, str], MirrorPosition] = {}
        self.pending_late_orders: dict[tuple[str, str, str], str] = {}
        self._last_save: float = 0

    def get_mirror(self, leader_name: str, symbol: str, side: str) -> MirrorPosition | None:
        return self.mirror_positions.get((leader_name, symbol, side))

    def set_mirror(self, pos: MirrorPosition):
        key = (pos.leader_name, pos.symbol, pos.position_side)
        self.mirror_positions[key] = pos

    def remove_mirror(self, leader_name: str, symbol: str, side: str):
        key = (leader_name, symbol, side)
        self.mirror_positions.pop(key, None)

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "mirrors": {
                f"{k[0]}|{k[1]}|{k[2]}": {
                    "leader_name": v.leader_name,
                    "symbol": v.symbol,
                    "position_side": v.position_side,
                    "our_quantity": str(v.our_quantity),
                    "leader_quantity_at_sync": str(v.leader_quantity_at_sync),
                    "opened_at": v.opened_at,
                }
                for k, v in self.mirror_positions.items()
            },
            "pending_late_orders": {
                "|".join(key): order_id
                for key, order_id in self.pending_late_orders.items()
            },
            "saved_at": time(),
        }
        STATE_FILE.write_text(json.dumps(data, cls=DecimalEncoder, indent=2))
        self._last_save = time()
        logger.debug("State saved to %s", STATE_FILE)

    def load(self):
        if not STATE_FILE.exists():
            logger.info("No state file found, starting fresh")
            return

        try:
            data = json.loads(STATE_FILE.read_text())
            mirrors_data = data.get("mirrors", {})
            logger.info("Loading state file with %d mirrors...", len(mirrors_data))

            for key_str, v in mirrors_data.items():
                pos = MirrorPosition(
                    leader_name=v["leader_name"],
                    symbol=v["symbol"],
                    position_side=v["position_side"],
                    our_quantity=Decimal(v["our_quantity"]),
                    leader_quantity_at_sync=Decimal(v["leader_quantity_at_sync"]),
                    opened_at=v["opened_at"],
                )
                self.set_mirror(pos)
                logger.info(
                    "Loaded mirror: %s %s %s (our_qty=%s, leader_qty=%s)",
                    pos.leader_name, pos.symbol, pos.position_side,
                    pos.our_quantity, pos.leader_quantity_at_sync
                )
            pending_data = data.get("pending_late_orders", {})
            for key_str, order_id in pending_data.items():
                key = tuple(key_str.split("|", 2))
                if len(key) == 3 and order_id:
                    self.pending_late_orders[key] = str(order_id)
            logger.info("✅ Loaded %d mirror positions from state", len(self.mirror_positions))
        except Exception as e:
            logger.error("Failed to load state: %s, starting fresh", e, exc_info=True)

    def maybe_save(self, interval: float = 30.0):
        if time() - self._last_save >= interval:
            self.save()
