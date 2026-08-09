import logging
from decimal import Decimal

from src.models import LeaderPosition, SignalType, TradeSignal

logger = logging.getLogger(__name__)


def detect_changes(
    leader_name: str,
    old_positions: dict[tuple[str, str], LeaderPosition],
    new_positions: list[LeaderPosition],
) -> list[TradeSignal]:
    """Compare old and new positions, produce trade signals."""
    new_map = {pos.key: pos for pos in new_positions}
    signals: list[TradeSignal] = []

    # New positions (opened)
    for key, pos in new_map.items():
        if key not in old_positions:
            side = "buy" if pos.is_long else "sell"
            signals.append(TradeSignal(
                signal_type=SignalType.OPEN,
                leader_name=leader_name,
                symbol=pos.symbol,
                side=side,
                position_side=pos.position_side,
                quantity=abs(pos.position_amount),
                leverage=pos.leverage,
                leader_notional=pos.notional_value,
                reason=f"Leader opened {pos.symbol} {pos.position_side}",
                leader_new_quantity=abs(pos.position_amount),
            ))

    # Closed positions
    for key, old_pos in old_positions.items():
        if key not in new_map:
            side = "sell" if old_pos.is_long else "buy"
            signals.append(TradeSignal(
                signal_type=SignalType.CLOSE,
                leader_name=leader_name,
                symbol=old_pos.symbol,
                side=side,
                position_side=old_pos.position_side,
                quantity=abs(old_pos.position_amount),
                leverage=old_pos.leverage,
                leader_notional=old_pos.notional_value,
                reason=f"Leader closed {old_pos.symbol} {old_pos.position_side}",
                leader_old_quantity=abs(old_pos.position_amount),
                leader_new_quantity=Decimal(0),
            ))

    # Modified positions (increase / decrease)
    for key in set(old_positions) & set(new_map):
        old_pos = old_positions[key]
        new_pos = new_map[key]
        old_amt = abs(old_pos.position_amount)
        new_amt = abs(new_pos.position_amount)

        # 🚨 检查方向反转（BOTH 模式下的特殊处理）
        old_is_long = old_pos.position_amount > 0
        new_is_long = new_pos.position_amount > 0

        if old_is_long != new_is_long and old_amt > 0:
            # 方向反转！先平后开
            logger.warning(
                "🔄 Direction reversal detected: %s %s -> %s",
                old_pos.symbol,
                "LONG" if old_is_long else "SHORT",
                "LONG" if new_is_long else "SHORT"
            )

            # 1. 平仓信号
            close_side = "sell" if old_is_long else "buy"
            signals.append(TradeSignal(
                signal_type=SignalType.CLOSE,
                leader_name=leader_name,
                symbol=old_pos.symbol,
                side=close_side,
                position_side=old_pos.position_side,
                quantity=old_amt,
                leverage=old_pos.leverage,
                leader_notional=old_pos.notional_value,
                reason=f"Leader closed {old_pos.symbol} (direction reversal)",
                leader_old_quantity=old_amt,
                leader_new_quantity=Decimal(0),
            ))

            # 2. 开仓信号（如果新仓位不为0）
            if new_amt > 0:
                open_side = "buy" if new_is_long else "sell"
                signals.append(TradeSignal(
                    signal_type=SignalType.OPEN,
                    leader_name=leader_name,
                    symbol=new_pos.symbol,
                    side=open_side,
                    position_side=new_pos.position_side,
                    quantity=new_amt,
                    leverage=new_pos.leverage,
                    leader_notional=new_pos.notional_value,
                    reason=f"Leader opened {new_pos.symbol} (direction reversal)",
                    leader_new_quantity=new_amt,
                ))

            continue  # 跳过后面的 increase/decrease 逻辑

        # 正常的加减仓（方向未变）
        if new_amt > old_amt:
            delta = new_amt - old_amt
            side = "buy" if new_pos.is_long else "sell"
            signals.append(TradeSignal(
                signal_type=SignalType.INCREASE,
                leader_name=leader_name,
                symbol=new_pos.symbol,
                side=side,
                position_side=new_pos.position_side,
                quantity=delta,
                leverage=new_pos.leverage,
                leader_notional=new_pos.notional_value,
                reason=f"Leader increased {new_pos.symbol} by {delta}",
                leader_old_quantity=old_amt,
                leader_new_quantity=new_amt,
            ))
        elif new_amt < old_amt:
            delta = old_amt - new_amt
            side = "sell" if new_pos.is_long else "buy"
            signals.append(TradeSignal(
                signal_type=SignalType.DECREASE,
                leader_name=leader_name,
                symbol=new_pos.symbol,
                side=side,
                position_side=new_pos.position_side,
                quantity=delta,
                leverage=new_pos.leverage,
                leader_notional=new_pos.notional_value,
                reason=f"Leader decreased {new_pos.symbol} by {delta}",
                leader_old_quantity=old_amt,
                leader_new_quantity=new_amt,
            ))

    return signals
