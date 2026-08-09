"""Construction and reload rules for configured Binance position sources."""

from __future__ import annotations

from src.config import Config
from src.position_source import PositionSource
from src.poller import Poller


def create_position_source(config: Config) -> PositionSource:
    """Create the configured source without exposing it to the coordinator."""
    source_type = config.binance_source.type.lower()
    if source_type == "http":
        return Poller(config.binance_web)
    if source_type == "browser":
        # Keep Playwright isolated from normal HTTP-only deployments.
        from src.binance_auth import BinanceBrowserPositionSource
        return BinanceBrowserPositionSource(config.binance_source.browser)
    if source_type == "hybrid":
        enabled_leader = next((leader for leader in config.leaders if leader.enabled), None)
        if enabled_leader is None:
            raise ValueError("Hybrid Binance source requires at least one leader")
        from src.binance_session import HybridBinancePositionSource
        return HybridBinancePositionSource(
            Poller(config.binance_web),
            config.binance_source.browser,
            config.binance_source.session,
            enabled_leader.portfolio_id,
        )
    raise ValueError(
        f"Unsupported binance_source.type: {config.binance_source.type!r}; "
        "expected 'http', 'browser', or 'hybrid'"
    )


def reload_position_source(source: PositionSource, old_config: Config, new_config: Config) -> None:
    """Reload settings that are safe without recreating a live source.

    Changing authentication mechanism changes process resources (aiohttp versus
    a persistent Chromium context), so it intentionally requires a controlled
    service restart instead of switching underneath active poll loops.
    """
    old_type = old_config.binance_source.type.lower()
    new_type = new_config.binance_source.type.lower()
    if old_type != new_type:
        raise ValueError("Changing binance_source.type requires restarting the service")

    if new_type == "http":
        source.reload(new_config.binance_web)  # type: ignore[attr-defined]
    elif new_type == "browser":
        source.reload(new_config.binance_source.browser)  # type: ignore[attr-defined]
    else:
        source.reload(  # type: ignore[attr-defined]
            new_config.binance_source.browser,
            new_config.binance_source.session,
            new_config.binance_web,
        )
