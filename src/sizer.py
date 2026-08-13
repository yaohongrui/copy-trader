import logging
from decimal import Decimal, ROUND_DOWN

from src.config import LeaderConfig, RiskConfig
from src.models import MirrorPosition, SignalType, TradeSignal

logger = logging.getLogger(__name__)


class Sizer:
    def __init__(self, risk_config: RiskConfig):
        self._risk = risk_config

    def calculate(
        self,
        signal: TradeSignal,
        leader_cfg: LeaderConfig,
        my_margin: Decimal,
        my_mirror: MirrorPosition | None,
        mark_price: Decimal,
        current_quantity: Decimal | None = None,
        precision: int = 8,
    ) -> Decimal | None:
        """
        Calculate the quantity to trade.

        Returns None if the trade should be skipped (risk limit, blacklist, etc).
        """
        logger.info(
            "[Sizer] Calculating for %s: signal_type=%s, my_margin=%s, mark_price=%s",
            signal.symbol, signal.signal_type.value, my_margin, mark_price
        )

        if signal.symbol in self._risk.blacklist:
            logger.info("Skipping blacklisted symbol: %s", signal.symbol)
            return None

        leader_margin = Decimal(str(leader_cfg.total_margin))
        coefficient = Decimal(str(leader_cfg.coefficient))

        if signal.signal_type == SignalType.DECREASE:
            leader_old_amt = signal.leader_old_quantity
            if leader_old_amt is None and my_mirror is not None:
                leader_old_amt = my_mirror.leader_quantity_at_sync
            if leader_old_amt is None or leader_old_amt <= 0:
                logger.warning("[Sizer] DECREASE but no mirror found, skipping")
                return None

            close_ratio = signal.quantity / leader_old_amt
            base_qty = current_quantity
            if base_qty is None and my_mirror is not None:
                base_qty = abs(my_mirror.our_quantity)
            if base_qty is None or base_qty <= 0:
                return None
            qty = base_qty * close_ratio

            # 防止基数过期导致比例 > 1，卖出超过当前持仓
            if qty > base_qty:
                logger.warning(
                    "[Sizer] DECREASE %.6f capped to current position %.6f",
                    qty, base_qty
                )
                qty = base_qty

            logger.info(
                "[Sizer] DECREASE ratio: leader_delta=%s / leader_old=%s = %.6f, our_qty=%s -> %.6f",
                signal.quantity, leader_old_amt, close_ratio,
                base_qty, qty
            )

            # 强制最小减仓量，避免小额减仓被跳过累积偏差（但不超出现有持仓）
            min_qty = Decimal(10) ** -precision  # 例如：0.001
            if qty > 0 and qty < min_qty:
                logger.warning(
                    "[Sizer] DECREASE %.6f too small, forcing minimum %.3f",
                    qty, min_qty
                )
                qty = min(min_qty, base_qty)

            return self._round_qty(qty, precision)

        # A configured fixed balance makes sizing deterministic and avoids
        # depending on the live exchange equity for OPEN/INCREASE orders.
        if self._risk.fixed_balance_usdt is not None:
            fixed_balance = Decimal(str(self._risk.fixed_balance_usdt))
            if fixed_balance <= 0:
                raise ValueError("risk.fixed_balance_usdt must be positive")
            logger.info(
                "[Sizer] Using fixed sizing balance: %s (live margin=%s)",
                fixed_balance, my_margin,
            )
            my_margin = fixed_balance

        # OPEN or INCREASE: apply the formula
        # 新公式：(my_notional / my_margin) = coefficient × (leader_notional / leader_margin)
        # 推导：my_notional = coefficient × (leader_notional / leader_margin) × my_margin
        leader_notional = abs(signal.leader_notional)  # Use absolute value
        my_notional = coefficient * (leader_notional / leader_margin) * my_margin

        logger.info(
            "[Sizer] Formula: %s × (%s / %s) × %s = %s",
            coefficient, leader_notional, leader_margin, my_margin, my_notional
        )

        my_qty = my_notional / mark_price

        logger.info(
            "[Sizer] Calculated quantity: %s (notional=%s / price=%s)",
            my_qty, my_notional, mark_price
        )

        # For INCREASE, only trade the delta
        if signal.signal_type == SignalType.INCREASE:
            base_qty = current_quantity
            if base_qty is None and my_mirror:
                base_qty = abs(my_mirror.our_quantity)
            if base_qty is not None:
                # 有记录，计算增量
                target_notional = my_notional
                current_notional = base_qty * mark_price
                delta_notional = target_notional - current_notional

                logger.info(
                    "[Sizer] INCREASE delta: target=%s current=%s delta=%s",
                    target_notional, current_notional, delta_notional
                )

                if delta_notional <= 0:
                    logger.warning("[Sizer] INCREASE delta <= 0, skipping")
                    return None
                my_qty = delta_notional / mark_price
            else:
                # 没有记录（可能系统重启），当作 OPEN 处理
                logger.warning(
                    "[Sizer] INCREASE but no mirror found, treating as OPEN"
                )
                # my_qty 已经计算好了，直接使用

        rounded_qty = self._round_qty(my_qty, precision)
        logger.info(
            "[Sizer] Final quantity after rounding: %s (precision=%d)",
            rounded_qty, precision
        )

        if rounded_qty <= 0:
            logger.warning("[Sizer] Quantity rounded to zero, skipping")
            return None

        return rounded_qty

    def _round_qty(self, qty: Decimal, precision: int) -> Decimal:
        step = Decimal(10) ** -precision
        return qty.quantize(step, rounding=ROUND_DOWN)
