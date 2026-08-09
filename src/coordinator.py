import asyncio
import logging
import random
from datetime import datetime, timedelta
from decimal import Decimal
from time import time

from src.config import Config, LeaderConfig
from src.bitget_client import BitgetSymbolUnavailableError
from src.detector import detect_changes
from src.health import HealthReporter
from src.health_db import HealthDatabase
from src.models import LeaderState, MirrorPosition, SignalType, TradeSignal
from src.notifier import Notifier
from src.poller import PollAuthError, PollError
from src.position_source_factory import create_position_source, reload_position_source
from src.sizer import Sizer
from src.state import State

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(self, config: Config):
        self._config = config
        self._poller = create_position_source(config)

        if config.execution.exchange.lower() != "bitget":
            raise ValueError("Only Bitget UTA is supported; set execution.exchange to 'bitget'")
        from src.bitget_account import BitgetAccount
        from src.bitget_executor import BitgetExecutor

        self._account = BitgetAccount(
            api_key=config.execution.api_key,
            api_secret=config.execution.api_secret,
            api_passphrase=config.execution.api_passphrase,
        )
        self._executor = BitgetExecutor(self._account)
        logger.info("Using Bitget Unified Trading Account module")

        self._sizer = Sizer(config.risk)
        self._notifier = Notifier(config.notifications_telegram)
        self._health_database = HealthDatabase(config.health_database.path)
        self._state = State()
        self._health = HealthReporter(
            config, self._poller, self._account, self._state,
            self._notifier, self._format_account_position,
            self._health_database,
        )
        self._signal_queue: asyncio.Queue[TradeSignal] = asyncio.Queue()
        self._running = False
        self._paused = False
        self._stopped = False
        self._poll_tasks: dict[str, asyncio.Task] = {}  # leader_name -> task，热重载用
        self._health_task: asyncio.Task | None = None
        self._telegram_task: asyncio.Task | None = None
        self._telegram_offset = 0
        self._unavailable_symbols: set[str] = set()
        self._unavailable_notified: set[str] = set()

    async def start(self):
        logger.info("Starting coordinator...")
        await self._poller.start()
        await self._account.start()
        await self._notifier.start()
        self._state.load()

        self._running = True

        # 获取账户余额并发送启动通知
        try:
            balance = await self._account.get_total_margin()
        except Exception as e:
            balance = Decimal(0)
            logger.exception("Initial account check failed: %s", e)
            await self._notifier.notify_error(f"Initial account check failed: {e}")
        enabled_leaders = [l.name for l in self._config.leaders if l.enabled]

        await self._notifier.send(
            f"🚀 <b>[System Started]</b>\n"
            f"Exchange: Bitget UTA\n"
            f"Balance: {balance} USDT\n"
            f"Leaders: {', '.join(enabled_leaders)}\n"
            f"Leverage: up to 50x Cross Margin"
        )

        tasks = []
        for leader_cfg in self._config.leaders:
            if leader_cfg.enabled:
                task = asyncio.create_task(
                    self._poll_loop(leader_cfg),
                    name=f"poll_{leader_cfg.name}",
                )
                self._poll_tasks[leader_cfg.name] = task
                tasks.append(task)

        tasks.append(asyncio.create_task(
            self._process_signals(),
            name="signal_processor",
        ))
        self._health_task = asyncio.create_task(
            self._health_check_loop(),
            name="hourly_health_check",
        )
        if self._notifier.can_receive_commands:
            self._telegram_task = asyncio.create_task(
                self._telegram_command_loop(),
                name="telegram_command_listener",
            )
            logger.info("Telegram command listener started")

        await asyncio.gather(*tasks)

    async def stop(self):
        """停止系统。幂等：重复调用不会重复发送通知/关闭会话。"""
        if self._stopped:
            return
        self._stopped = True
        logger.info("Stopping coordinator...")
        self._running = False
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        if self._telegram_task and not self._telegram_task.done():
            self._telegram_task.cancel()
        self._state.save()
        await self._notifier.send("⏹️ <b>[System Stopped]</b>")
        await self._notifier.stop()
        await self._poller.stop()
        await self._account.stop()

    def pause(self):
        self._paused = True
        logger.warning("Coordinator paused (likely cookie expired)")

    def resume(self):
        self._paused = False
        logger.info("Coordinator resumed")

    async def reload(self, new_config: Config):
        """热重载配置，并同步带单员轮询任务。"""
        logger.info("Reloading config...")
        reload_position_source(self._poller, self._config, new_config)
        self._config = new_config
        self._sizer = Sizer(new_config.risk)

        # 重建 notifier（Telegram 配置可能变化）
        old_notifier = self._notifier
        self._notifier = Notifier(new_config.notifications_telegram)
        await old_notifier.stop()
        await self._notifier.start()
        self._health = HealthReporter(
            new_config, self._poller, self._account, self._state,
            self._notifier, self._format_account_position,
            self._health_database,
        )

        if self._telegram_task and not self._notifier.can_receive_commands:
            self._telegram_task.cancel()
            self._telegram_task = None
        elif self._telegram_task is None and self._notifier.can_receive_commands:
            self._telegram_task = asyncio.create_task(
                self._telegram_command_loop(),
                name="telegram_command_listener",
            )
            logger.info("Reload: started Telegram command listener")

        # 新增的 leader 启动轮询；被移除的 leader 由 poll loop 自行检测退出
        for leader_cfg in new_config.leaders:
            if leader_cfg.enabled and leader_cfg.name not in self._poll_tasks:
                task = asyncio.create_task(
                    self._poll_loop(leader_cfg),
                    name=f"poll_{leader_cfg.name}",
                )
                self._poll_tasks[leader_cfg.name] = task
                logger.info("Reload: started poll loop for %s", leader_cfg.name)

        self.resume()
        logger.info("Config reloaded successfully")

    def _is_leader_enabled(self, name: str) -> bool:
        return any(l.name == name and l.enabled for l in self._config.leaders)

    async def _poll_loop(self, leader_cfg: LeaderConfig):
        """Continuously poll a single leader's positions."""
        leader_name = leader_cfg.name
        portfolio_id = leader_cfg.portfolio_id
        interval = self._config.polling.interval_seconds
        jitter_ms = self._config.polling.jitter_ms

        if leader_name not in self._state.leader_states:
            self._state.leader_states[leader_name] = LeaderState(
                leader_name=leader_name,
                portfolio_id=portfolio_id,
            )

        leader_state = self._state.leader_states[leader_name]
        logger.info("Polling started for leader: %s (%s)", leader_name, portfolio_id)

        while self._running:
            try:
                # 热重载后 leader 被禁用时，本循环自行退出
                if not self._is_leader_enabled(leader_name):
                    logger.info("Leader %s no longer enabled, stopping poll loop", leader_name)
                    break

                if self._paused:
                    await asyncio.sleep(5)
                    continue

                try:
                    positions = await self._poller.fetch_positions(portfolio_id)
                except PollError as e:
                    leader_state.consecutive_errors += 1
                    logger.error("Poll failed for %s (%d consecutive): %s",
                                 portfolio_id, leader_state.consecutive_errors, e)
                    if leader_state.consecutive_errors >= 3:
                        self.pause()
                        if isinstance(e, PollAuthError):
                            await self._notifier.notify_cookie_expired(
                                self._config.binance_source.type
                            )
                        else:
                            await self._notifier.notify_error(
                                f"Position poll failed ({portfolio_id}): {e}"
                            )
                    await asyncio.sleep(interval * 2)
                    continue

                leader_state.consecutive_errors = 0
                leader_state.last_poll_time = time()

                # 首次成功拉取时先做一次重连校验。首轮数据不能直接当成全部 OPEN
                # 信号，否则只要已有部分仓位，旧的去重逻辑就会跳过整个补仓动作。
                if not leader_state.initialized:
                    await self._reconcile_after_reconnect(leader_cfg, positions)
                    leader_state.initialized = True
                    signals = []
                else:
                    signals = detect_changes(leader_name, leader_state.positions, positions)
                leader_state.positions = {pos.key: pos for pos in positions}

                for signal in signals:
                    await self._signal_queue.put(signal)

                self._state.maybe_save()
                jitter = random.uniform(0, jitter_ms / 1000)
                await asyncio.sleep(interval + jitter)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 兜底：避免单个 leader 的意外异常导致整个系统崩溃/重启
                logger.exception("Unexpected error in poll loop for %s: %s", leader_name, e)
                await self._notifier.notify_error(
                    f"Polling loop exception: {leader_name} - {e}"
                )
                await asyncio.sleep(interval)

    async def _process_signals(self):
        """Process trade signals sequentially."""
        while self._running:
            try:
                signal = await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self._handle_signal(signal)
            except Exception as e:
                logger.exception("Error processing signal: %s", e)
                await self._notifier.notify_error(f"Signal processing exception: {e}")

    async def _health_check_loop(self):
        """Run a read-only health check at every local full hour."""
        while self._running:
            now = datetime.now().astimezone()
            next_hour = (now.replace(minute=0, second=0, microsecond=0)
                         + timedelta(hours=1))
            wait_seconds = max(1.0, (next_hour - now).total_seconds())
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                raise

            if self._running:
                try:
                    await self._run_health_check()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("Hourly health check failed: %s", e)
                    await self._notifier.notify_error(f"Hourly health check failed: {e}")

    async def _telegram_command_loop(self):
        """Listen for commands from the configured Telegram chat."""
        while self._running:
            updates = await self._notifier.get_updates(self._telegram_offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._telegram_offset = max(self._telegram_offset, update_id + 1)

                message = update.get("message", {})
                chat = message.get("chat", {})
                if not self._notifier.is_authorized_chat(chat.get("id")):
                    continue
                text = message.get("text", "")
                command = text.strip().split()[0].lower().lstrip("/") if text.strip() else ""
                command = command.split("@", 1)[0]
                if command == "s":
                    await self._send_telegram_status()
                elif command == "p":
                    await self._send_telegram_positions()

            await asyncio.sleep(0.5)

    async def _send_telegram_status(self):
        await self._notifier.send(await self._health.build_status_report(self._paused))

    async def _send_telegram_positions(self):
        try:
            positions = await self._account.get_positions()
        except Exception as e:
            await self._notifier.send(f"<b>[Current Positions]</b>\nFailed: {e}")
            return

        lines = ["<b>[Current Positions]</b>"]
        if positions:
            lines.extend(self._format_account_position(position) for position in positions)
        else:
            lines.append("No open positions")
        await self._notifier.send("\n".join(lines))

    async def _run_health_check(self):
        await self._health.send_hourly_report()

    def _format_account_position(self, position: dict) -> str:
        symbol = position.get('contract', '?')
        quantity = position.get('size', 0)
        entry = self._format_position_decimal(position.get('entry_price', '?'))
        mark = position.get('mark_price', '?')
        pnl = self._format_position_decimal(position.get('unrealized_pnl', '0'))
        try:
            roi = f"{Decimal(str(position.get('roi_pct', '0'))):.2f}"
        except (ArithmeticError, ValueError):
            roi = "0.00"
        return f"{symbol} qty={quantity} entry={entry} mark={mark} " \
               f"unrealized_pnl={pnl} ROI={roi}%"

    @staticmethod
    def _format_position_decimal(value) -> str:
        try:
            return f"{Decimal(str(value)):.2f}"
        except (ArithmeticError, ValueError):
            return str(value)

    async def _handle_signal(self, signal: TradeSignal):
        """Process a single trade signal: check conflicts, size, execute."""
        if signal.symbol in self._unavailable_symbols:
            logger.info("Skipping unavailable Bitget symbol: %s", signal.symbol)
            return

        logger.info("Processing signal: %s %s %s from %s",
                    signal.signal_type.value, signal.side, signal.symbol, signal.leader_name)

        # Conflict check for OPEN signals
        if signal.signal_type == SignalType.OPEN:
            if self._has_conflict(signal):
                logger.warning("Conflict detected for %s, skipping", signal.symbol)
                await self._notifier.notify_skipped(
                    leader=signal.leader_name,
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    side=signal.side,
                    reason="conflicts with another leader position",
                )
                return

            # 去重检查：查询实际持仓，避免重复开仓；同时把 mirror 同步到实际
            # 持仓和带单员当前数量，避免后续加减仓按旧基数计算
            existing = await self._has_existing_position(signal.symbol)
            if existing:
                actual_long = self._position_is_long(signal.symbol, existing)
                expected_long = signal.side == "buy"
                if actual_long is not None and actual_long != expected_long:
                    logger.error(
                        "[Dedup] Direction mismatch for %s: existing=%s signal=%s; "
                        "automatic reversal skipped",
                        signal.symbol,
                        "LONG" if actual_long else "SHORT",
                        "LONG" if expected_long else "SHORT",
                    )
                    await self._notifier.notify_error(
                        f"Direction mismatch for {signal.symbol}; automatic reversal skipped"
                    )
                    return
                await self._sync_mirror_after_dedup(signal, existing)
                logger.warning(
                    "⚠️ [Dedup] Already have position in %s, skipping OPEN signal",
                    signal.symbol
                )
                await self._notifier.send(
                    f"⚠️ <b>[Duplicate Prevented]</b>\n"
                    f"Symbol: {signal.symbol}\n"
                    f"Already have open position, skipped duplicate OPEN"
                )
                return

        # Get leader config
        leader_cfg = next(
            (l for l in self._config.leaders if l.name == signal.leader_name), None
        )
        if not leader_cfg:
            await self._notifier.notify_error(
                f"Signal ignored: leader configuration not found ({signal.leader_name})"
            )
            return

        # Get current mirror position
        mirror = self._state.get_mirror(
            signal.leader_name, signal.symbol, signal.position_side
        )

        # Reducing orders must be based on the live account position.  The
        # mirror is only bookkeeping and can be stale after a restart, a
        # manual trade, or a partial fill from an earlier order.
        actual_qty: Decimal | None = None
        if signal.signal_type in (SignalType.CLOSE, SignalType.DECREASE, SignalType.INCREASE):
            try:
                actual_qty = await self._get_actual_position_quantity(signal.symbol)
            except Exception as e:
                logger.error(
                    "Failed to read actual position for %s before %s: %s",
                    signal.symbol, signal.signal_type.value, e,
                )
                await self._notifier.notify_error(
                    f"Position lookup failed: {signal.symbol} {signal.signal_type.value} - {e}"
                )
                return

            if actual_qty <= 0 and signal.signal_type != SignalType.INCREASE:
                logger.warning(
                    "Skipping %s for %s: no held position (mirror=%s)",
                    signal.signal_type.value, signal.symbol,
                    mirror.our_quantity if mirror else None,
                )
                self._state.remove_mirror(
                    signal.leader_name, signal.symbol, signal.position_side
                )
                await self._notifier.notify_skipped(
                    leader=signal.leader_name,
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    side=signal.side,
                    reason="no held position on Bitget",
                )
                return

            if signal.signal_type == SignalType.CLOSE:
                # A leader close means close the entire live account position,
                # regardless of whether local mirror state exists.
                quantity = actual_qty
                logger.info(
                    "[Close] Using full live position for %s: qty=%s (mirror=%s)",
                    signal.symbol, quantity,
                    mirror.our_quantity if mirror else None,
                )

        # Get mark price from exchange
        mark_price = Decimal(0)
        if signal.signal_type != SignalType.CLOSE:
            try:
                mark_price = await self._get_mark_price(signal.symbol)
            except BitgetSymbolUnavailableError as e:
                await self._handle_unavailable_symbol(signal.symbol, str(e))
                return
            except Exception as e:
                logger.error("Failed to get price for %s: %s", signal.symbol, e)
                await self._notifier.notify_error(f"Price fetch failed: {signal.symbol}")
                return

        # Calculate quantity for opens/increases and proportional decreases.
        if signal.signal_type != SignalType.CLOSE:
            try:
                my_margin = await self._account.get_total_margin()
                quantity = self._sizer.calculate(
                    signal=signal,
                    leader_cfg=leader_cfg,
                    my_margin=my_margin,
                    my_mirror=mirror,
                    mark_price=mark_price,
                    current_quantity=actual_qty,
                )
            except Exception as e:
                logger.exception("Failed to calculate order size for %s: %s", signal.symbol, e)
                await self._notifier.notify_error(
                    f"Order sizing failed: {signal.symbol} {signal.signal_type.value} - {e}"
                )
                return

            if signal.signal_type == SignalType.DECREASE and quantity is not None:
                if quantity > actual_qty:
                    logger.warning(
                        "[Reduce] Capping %s quantity for %s: requested=%s actual=%s",
                        signal.signal_type.value, signal.symbol, quantity, actual_qty,
                    )
                    quantity = actual_qty

        if quantity is None or quantity <= 0:
            reason = "quantity too small or zero" if quantity == 0 else "calculated quantity is None"
            logger.info("Sizer returned skip for %s: %s", signal.symbol, reason)
            await self._notifier.notify_skipped(
                leader=signal.leader_name,
                symbol=signal.symbol,
                signal_type=signal.signal_type.value,
                side=signal.side,
                reason=reason
            )
            return

        # Execute
        result = await self._executor.execute(signal, quantity)

        if result.success:
            # 使用按 Bitget 合约步进取整后的实际请求数量更新镜像状态。
            filled_qty = result.filled_qty if result.filled_qty > 0 else quantity
            self._update_state_after_trade(signal, filled_qty, actual_qty)
            logger.info(
                "✅ [Trade] Executed successfully: type=%s side=%s symbol=%s "
                "requested_qty=%s filled_qty=%s avg_price=%s order_id=%s",
                signal.signal_type.value, signal.side, signal.symbol,
                quantity, filled_qty, result.avg_price, result.order_id,
            )
            await self._notifier.notify_trade(
                leader=signal.leader_name,
                symbol=signal.symbol,
                side=signal.side,
                qty=str(filled_qty),
                signal_type=signal.signal_type.value,
                avg_price=str(result.avg_price),
                order_id=result.order_id,
            )
        else:
            logger.error(
                "❌ [Trade] Order failed: type=%s side=%s symbol=%s error=%s",
                signal.signal_type.value, signal.side, signal.symbol, result.error,
            )
            await self._notifier.notify_error(
                f"Order failed: {signal.symbol} {signal.side} - {result.error}"
            )

    def _has_conflict(self, signal: TradeSignal) -> bool:
        """Check if opening this position conflicts with another leader's position."""
        if self._config.risk.conflict_resolution != "skip":
            return False

        for key, mirror in self._state.mirror_positions.items():
            if mirror.symbol == signal.symbol and mirror.leader_name != signal.leader_name:
                return True
        return False

    def _update_state_after_trade(
        self,
        signal: TradeSignal,
        filled_qty: Decimal,
        starting_actual_qty: Decimal | None = None,
    ):
        """Update mirror position state after successful trade（用实际成交数量，而非 sizer 计算值）。"""
        if signal.signal_type == SignalType.OPEN:
            self._state.set_mirror(MirrorPosition(
                leader_name=signal.leader_name,
                symbol=signal.symbol,
                position_side=signal.position_side,
                our_quantity=filled_qty,
                leader_quantity_at_sync=signal.quantity,
            ))
        elif signal.signal_type == SignalType.CLOSE:
            # CLOSE is intentionally an account-level full close.  Remove all
            # mirrors for this contract so stale per-leader records cannot
            # generate more reduce orders after the position is gone.
            for key, mirror in list(self._state.mirror_positions.items()):
                if mirror.symbol == signal.symbol:
                    self._state.mirror_positions.pop(key, None)
        elif signal.signal_type == SignalType.INCREASE:
            mirror = self._state.get_mirror(signal.leader_name, signal.symbol, signal.position_side)
            if mirror is None:
                mirror = MirrorPosition(
                    leader_name=signal.leader_name,
                    symbol=signal.symbol,
                    position_side=signal.position_side,
                    our_quantity=(starting_actual_qty or Decimal(0)) + filled_qty,
                    leader_quantity_at_sync=(
                        signal.leader_new_quantity or signal.quantity
                    ),
                )
                self._state.set_mirror(mirror)
            else:
                mirror.our_quantity = (starting_actual_qty or mirror.our_quantity) + filled_qty
                mirror.leader_quantity_at_sync = (
                    signal.leader_new_quantity
                    or mirror.leader_quantity_at_sync + signal.quantity
                )
        elif signal.signal_type == SignalType.DECREASE:
            mirror = self._state.get_mirror(signal.leader_name, signal.symbol, signal.position_side)
            remaining = max(
                Decimal(0),
                (starting_actual_qty if starting_actual_qty is not None else (
                    mirror.our_quantity if mirror else Decimal(0)
                )) - filled_qty,
            )
            if mirror is None:
                mirror = MirrorPosition(
                    leader_name=signal.leader_name,
                    symbol=signal.symbol,
                    position_side=signal.position_side,
                    our_quantity=remaining,
                    leader_quantity_at_sync=(
                        signal.leader_new_quantity or Decimal(0)
                    ),
                )
                if remaining > 0:
                    self._state.set_mirror(mirror)
            else:
                mirror.our_quantity = remaining
                mirror.leader_quantity_at_sync = max(
                    Decimal(0),
                    signal.leader_new_quantity
                    if signal.leader_new_quantity is not None
                    else mirror.leader_quantity_at_sync - signal.quantity,
                )

    async def _get_mark_price(self, symbol: str) -> Decimal:
        """Get Bitget's mark price, used consistently for sizing."""
        ticker = await self._account.client.get_ticker(symbol)
        price = Decimal(str(ticker.get("markPrice", ticker.get("lastPrice", 0))))
        if price <= 0:
            raise ValueError(f"Invalid mark price for {symbol}: {price}")
        return price

    async def _get_existing_position(self, symbol: str):
        """Return the normalized Bitget position for a symbol, if present."""
        try:
            positions = await self._account.get_positions()

            for pos in positions:
                if pos.get('contract') == symbol:
                    size = abs(Decimal(str(pos.get('size', 0))))
                    if size > 0:
                        logger.info("[Dedup] Found existing position: %s size=%s", symbol, size)
                        return pos

            return None

        except Exception as e:
            logger.error("Failed to check existing positions: %s", e)
            # 查询失败时保守处理，返回 None 允许继续
            return None

    async def _get_actual_position_quantity(self, symbol: str) -> Decimal:
        """Return the live absolute position quantity from Bitget."""
        positions = await self._account.get_positions()
        for position in positions:
            if position.get("contract") != symbol:
                continue
            return abs(Decimal(str(position.get("size", 0))))
        return Decimal(0)

    async def _has_existing_position(self, symbol: str):
        return await self._get_existing_position(symbol)

    def _position_is_long(self, symbol: str, position: dict) -> bool | None:
        """Return actual position direction; None when the exchange response is unusable."""
        try:
            size = Decimal(str(position.get('size', 0)))
            return size > 0 if size != 0 else None
        except Exception:
            return None

    def _position_entry_price(self, position: dict) -> Decimal:
        """Read entry price from the normalized Bitget position."""
        for key in ('entry_price',):
            value = position.get(key)
            if value not in (None, '', '0', 0):
                try:
                    return Decimal(str(value))
                except Exception:
                    pass
        return Decimal(0)

    def _price_is_favorable(self, is_long: bool, market_price: Decimal, leader_entry: Decimal) -> bool:
        """Whether a new market entry is no worse than the leader's entry price."""
        if market_price <= 0 or leader_entry <= 0:
            return False
        return market_price <= leader_entry if is_long else market_price >= leader_entry

    async def _reconcile_after_reconnect(
        self, leader_cfg: LeaderConfig, positions: list
    ):
        """Reconcile missed leader changes after startup/reconnect.

        Only adds missing quantity when the current market price is at least as
        favorable as the leader entry price. It never automatically reduces an
        excess position or reverses a direction; those cases are logged for review.
        """
        logger.info("[Reconcile] Checking positions after reconnect: leader=%s", leader_cfg.name)

        actual_positions = await self._account.get_positions()
        actual_by_symbol: dict[str, dict] = {}
        for position in actual_positions:
            symbol = position.get('contract', '')
            if symbol:
                actual_by_symbol[symbol] = position

        seen_keys: set[tuple[str, str]] = set()
        for leader_pos in positions:
            seen_keys.add(leader_pos.key)
            signal = TradeSignal(
                signal_type=SignalType.OPEN,
                leader_name=leader_cfg.name,
                symbol=leader_pos.symbol,
                side="buy" if leader_pos.is_long else "sell",
                position_side=leader_pos.position_side,
                quantity=abs(leader_pos.position_amount),
                leverage=leader_pos.leverage,
                leader_notional=leader_pos.notional_value,
                reason="Reconnect reconciliation",
            )

            actual = actual_by_symbol.get(leader_pos.symbol)
            if actual:
                actual_long = self._position_is_long(leader_pos.symbol, actual)
                if actual_long is not None and actual_long != leader_pos.is_long:
                    logger.error(
                        "[Reconcile] Direction mismatch, no automatic action: %s leader=%s actual=%s",
                        leader_pos.symbol,
                        "LONG" if leader_pos.is_long else "SHORT",
                        "LONG" if actual_long else "SHORT",
                    )
                    await self._notifier.notify_error(
                        f"Reconcile direction mismatch: {leader_pos.symbol}; automatic action skipped"
                    )
                    continue

            try:
                market_price = await self._get_mark_price(leader_pos.symbol)
                target_qty = self._sizer.calculate(
                    signal=signal,
                    leader_cfg=leader_cfg,
                    my_margin=await self._account.get_total_margin(),
                    my_mirror=None,
                    mark_price=market_price,
                )
            except BitgetSymbolUnavailableError as e:
                await self._handle_unavailable_symbol(leader_pos.symbol, str(e))
                continue
            except Exception as e:
                logger.error("[Reconcile] Failed to calculate %s: %s", leader_pos.symbol, e)
                await self._notifier.notify_error(
                    f"Reconnect reconciliation failed: {leader_pos.symbol} - {e}"
                )
                continue

            if target_qty is None or target_qty <= 0:
                continue

            actual_qty = await self._position_size_in_coins(leader_pos.symbol, actual) if actual else Decimal(0)
            if actual and actual_qty is None:
                logger.error(
                    "[Reconcile] Cannot determine current quantity for %s; no automatic action",
                    leader_pos.symbol,
                )
                await self._notifier.notify_error(
                    f"Reconnect reconciliation cannot read current quantity: {leader_pos.symbol}"
                )
                continue
            missing_qty = target_qty - actual_qty

            if missing_qty <= 0:
                logger.info(
                    "[Reconcile] OK: %s actual=%s target=%s leader_entry=%s market=%s",
                    leader_pos.symbol, actual_qty, target_qty,
                    leader_pos.entry_price, market_price,
                )
                self._sync_reconciled_mirror(leader_cfg.name, leader_pos, actual_qty)
                continue

            favorable = self._price_is_favorable(
                leader_pos.is_long, market_price, leader_pos.entry_price
            )
            if not favorable:
                logger.warning(
                    "[Reconcile] Missing %s on %s, but price is unfavorable; no action "
                    "(leader_entry=%s market=%s target=%s actual=%s)",
                    missing_qty, leader_pos.symbol, leader_pos.entry_price,
                    market_price, target_qty, actual_qty,
                )
                await self._notifier.notify_skipped(
                    leader=leader_cfg.name,
                    symbol=leader_pos.symbol,
                    signal_type="reconcile",
                    side=signal.side,
                    reason=f"missing {missing_qty}, market price is worse than leader entry",
                )
                self._sync_reconciled_mirror(leader_cfg.name, leader_pos, actual_qty)
                continue

            logger.warning(
                "[Reconcile]補齐仓位: %s missing=%s target=%s actual=%s leader_entry=%s market=%s",
                leader_pos.symbol, missing_qty, target_qty, actual_qty,
                leader_pos.entry_price, market_price,
            )
            result = await self._executor.execute(signal, missing_qty)
            if result.success:
                filled_qty = result.filled_qty if result.filled_qty > 0 else missing_qty
                new_qty = actual_qty + filled_qty
                self._sync_reconciled_mirror(leader_cfg.name, leader_pos, new_qty)
                logger.info(
                    "✅ [Reconcile]补齐成功: %s filled=%s avg_price=%s order_id=%s",
                    leader_pos.symbol, filled_qty, result.avg_price, result.order_id,
                )
                await self._notifier.notify_trade(
                    leader=leader_cfg.name,
                    symbol=leader_pos.symbol,
                    side=signal.side,
                    qty=str(filled_qty),
                    signal_type="reconcile",
                    avg_price=str(result.avg_price),
                    order_id=result.order_id,
                )
            else:
                logger.error("[Reconcile]补齐失败: %s: %s", leader_pos.symbol, result.error)
                await self._notifier.notify_error(
                    f"Reconnect reconciliation order failed: {leader_pos.symbol} - {result.error}"
                )

        mirror_keys = [
            key for key, mirror in self._state.mirror_positions.items()
            if mirror.leader_name == leader_cfg.name
        ]
        for key in mirror_keys:
            _, symbol, position_side = key
            if (symbol, position_side) not in seen_keys:
                if symbol not in actual_by_symbol:
                    self._state.remove_mirror(leader_cfg.name, symbol, position_side)
                    logger.info(
                        "[Reconcile] Removed stale mirror: %s %s; "
                        "leader and local positions are both absent",
                        symbol, position_side,
                    )
                else:
                    logger.warning(
                        "[Reconcile] Leader position disappeared while disconnected: %s %s; "
                        "local position was not closed automatically",
                        symbol, position_side,
                    )

        self._state.save()

    async def _handle_unavailable_symbol(self, symbol: str, reason: str):
        """Disable a symbol for this run after Bitget says it is unavailable."""
        first_occurrence = symbol not in self._unavailable_symbols
        self._unavailable_symbols.add(symbol)
        if first_occurrence:
            logger.warning("[Symbol Disabled] Bitget symbol unavailable: %s (%s)", symbol, reason)
        if symbol not in self._unavailable_notified:
            self._unavailable_notified.add(symbol)
            await self._notifier.notify_skipped(
                leader="system",
                symbol=symbol,
                signal_type="unavailable",
                side="-",
                reason="Bitget has removed this symbol; all signals are skipped for this run",
            )

    def _sync_reconciled_mirror(self, leader_name: str, leader_pos, our_qty: Decimal):
        if our_qty <= 0:
            return
        self._state.set_mirror(MirrorPosition(
            leader_name=leader_name,
            symbol=leader_pos.symbol,
            position_side=leader_pos.position_side,
            our_quantity=our_qty,
            leader_quantity_at_sync=abs(leader_pos.position_amount),
        ))

    async def _position_size_in_coins(self, symbol: str, position: dict) -> Decimal | None:
        """Return absolute base-coin quantity from normalized Bitget data."""
        try:
            return abs(Decimal(str(position.get('size', 0))))
        except Exception as e:
            logger.error("Failed to convert position size for %s: %s", symbol, e)
            return None

    async def _sync_mirror_after_dedup(self, signal: TradeSignal, position: dict):
        """去重跳过 OPEN 时，把 mirror 同步到实际持仓和带单员当前数量，
        避免后续 INCREASE/DECREASE 用旧基数计算。"""
        our_qty = await self._position_size_in_coins(signal.symbol, position)
        if our_qty is None or our_qty <= 0:
            return

        mirror = self._state.get_mirror(signal.leader_name, signal.symbol, signal.position_side)
        if mirror is None:
            mirror = MirrorPosition(
                leader_name=signal.leader_name,
                symbol=signal.symbol,
                position_side=signal.position_side,
                our_quantity=our_qty,
                leader_quantity_at_sync=signal.quantity,
            )
        else:
            mirror.our_quantity = our_qty
            mirror.leader_quantity_at_sync = signal.quantity
        self._state.set_mirror(mirror)
        logger.info("[Dedup] Synced mirror %s: our_qty=%s, leader_qty=%s",
                    signal.symbol, our_qty, signal.quantity)
