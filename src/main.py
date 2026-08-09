import argparse
import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import load_config
from src.coordinator import Coordinator
from src.poller import PollAuthError, PollError, missing_cookie_fields
from src.position_source_factory import create_position_source


def setup_logging(level: str, file: str, max_size_mb: int, backup_count: int):
    log_path = Path(file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        file, maxBytes=max_size_mb * 1024 * 1024, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(file_handler)
    root.addHandler(console_handler)


async def run(config_path: str):
    config = load_config(config_path)
    setup_logging(
        config.logging.level,
        config.logging.file,
        config.logging.max_size_mb,
        config.logging.backup_count,
    )

    logger = logging.getLogger(__name__)
    coordinator = Coordinator(config)

    loop = asyncio.get_running_loop()

    def handle_shutdown():
        logger.info("Shutdown signal received")
        asyncio.ensure_future(coordinator.stop())

    def handle_reload():
        logger.info("Reload signal received, reloading config...")
        try:
            new_config = load_config(config_path)
        except Exception as e:
            logger.error("Config reload failed: %s", e)
            return
        # 交给 Coordinator 统一处理：更新仓位来源、风控、通知并同步轮询任务
        asyncio.create_task(coordinator.reload(new_config))

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
        loop.add_signal_handler(signal.SIGINT, handle_shutdown)
        loop.add_signal_handler(signal.SIGHUP, handle_reload)

    try:
        await coordinator.start()
    except KeyboardInterrupt:
        pass
    finally:
        await coordinator.stop()


async def validate(config_path: str):
    config = load_config(config_path)
    print(f"Config loaded: {len(config.leaders)} leader(s) configured")

    poller = create_position_source(config)
    try:
        await poller.start()
    except Exception as exc:
        print(f"  [Binance] FAILED - could not start position source: {exc}")
        print("\nValidation complete.")
        return

    try:
        for leader in config.leaders:
            if not leader.enabled:
                continue
            try:
                positions = await poller.fetch_positions(leader.portfolio_id)
                missing = missing_cookie_fields(config.binance_web.cookie)
                source_type = config.binance_source.type.lower()
                if source_type == "browser":
                    status = "BROWSER AUTH OK" if positions else "BROWSER AUTH OK - no positions returned"
                elif source_type == "hybrid":
                    status = "HYBRID HTTP OK" if positions else "HYBRID HTTP OK - no positions returned"
                elif missing:
                    status = f"INCOMPLETE COOKIE (missing {', '.join(missing)})"
                elif positions:
                    status = "API OK"
                else:
                    status = "API OK - no positions returned"
                print(f"  [{leader.name}] {status} - {len(positions)} active position(s)")
            except PollAuthError:
                auth_hint = (
                    "check browser login/profile"
                    if config.binance_source.type.lower() in {"browser", "hybrid"}
                    else "check cookie"
                )
                print(f"  [{leader.name}] FAILED - auth error ({auth_hint})")
            except PollError as e:
                print(f"  [{leader.name}] FAILED - {e}")
    finally:
        await poller.stop()

    if config.execution.exchange.lower() != "bitget":
        print("  [Account] FAILED - execution.exchange must be 'bitget'")
    else:
        from src.bitget_account import BitgetAccount
        account = BitgetAccount(
            config.execution.api_key, config.execution.api_secret,
            config.execution.api_passphrase,
        )
        try:
            await account.start()
            margin = await account.get_total_margin()
            print(f"  [Account] OK - UTA equity: {margin} USDT (Bitget)")
        except Exception as e:
            print(f"  [Account] FAILED - {e}")
        finally:
            await account.stop()

    print("\nValidation complete.")


def cli():
    parser = argparse.ArgumentParser(description="Futures Copy Trader")
    parser.add_argument("command", choices=["run", "validate"], help="Command to execute")
    parser.add_argument("--config", default="config/config.yaml", help="Config file path")
    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run(args.config))
    elif args.command == "validate":
        asyncio.run(validate(args.config))


if __name__ == "__main__":
    cli()
