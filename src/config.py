import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """Replace ${VAR} with environment variable value."""
    def replacer(match):
        var = match.group(1)
        env_val = os.environ.get(var)
        if env_val is None:
            raise ValueError(f"Environment variable {var} not set")
        return env_val
    return ENV_PATTERN.sub(replacer, value)


def _resolve_env_recursive(obj):
    """Walk config tree and resolve all ${VAR} references."""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_recursive(item) for item in obj]
    return obj


@dataclass
class LeaderConfig:
    name: str
    portfolio_id: str
    coefficient: float = 1.0
    total_margin: float = 50000.0
    enabled: bool = True


@dataclass
class PollingConfig:
    interval_seconds: float = 2.5
    jitter_ms: int = 500


@dataclass
class BinanceWebConfig:
    cookie: str = ""
    csrf_token: str = ""
    fvideo_id: str = ""
    fvideo_token: str = ""
    bnc_uuid: str = ""
    device_info: str = ""
    user_agent: str = ""


@dataclass
class BinanceBrowserConfig:
    """Persistent-browser settings; the profile is a login credential."""

    profile_dir: str = "data/binance-browser-profile"
    headless: bool = True
    timeout_ms: int = 30_000


@dataclass
class BinanceSessionConfig:
    """How often the browser refreshes the HTTP authentication material."""

    refresh_interval_hours: float = 36.0


@dataclass
class BinanceSourceConfig:
    """Choose how leader positions are read from Binance."""

    type: str = "http"
    browser: BinanceBrowserConfig = field(default_factory=BinanceBrowserConfig)
    session: BinanceSessionConfig = field(default_factory=BinanceSessionConfig)


@dataclass
class ExecutionConfig:
    exchange: str = "bitget"
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    sandbox: bool = True


@dataclass
class RiskConfig:
    blacklist: list[str] = field(default_factory=list)
    conflict_resolution: str = "skip"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/app.log"
    max_size_mb: int = 50
    backup_count: int = 5


@dataclass
class HealthDatabaseConfig:
    path: str = "data/health.db"


@dataclass
class Config:
    polling: PollingConfig
    binance_web: BinanceWebConfig
    binance_source: BinanceSourceConfig
    leaders: list[LeaderConfig]
    execution: ExecutionConfig
    risk: RiskConfig
    notifications_telegram: TelegramConfig
    logging: LoggingConfig
    health_database: HealthDatabaseConfig = field(default_factory=HealthDatabaseConfig)


def load_config(path: str | Path) -> Config:
    """Load config from YAML file, resolving ${ENV_VAR} references."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    resolved = _resolve_env_recursive(raw)

    polling = PollingConfig(**resolved.get("polling", {}))
    binance_web = BinanceWebConfig(**resolved.get("binance_web", {}))
    source_raw = resolved.get("binance_source", {})
    session = BinanceSessionConfig(**source_raw.get("session", {}))
    if session.refresh_interval_hours <= 0:
        raise ValueError("binance_source.session.refresh_interval_hours must be positive")
    binance_source = BinanceSourceConfig(
        type=source_raw.get("type", "http"),
        browser=BinanceBrowserConfig(**source_raw.get("browser", {})),
        session=session,
    )

    leaders = [
        LeaderConfig(**leader)
        for leader in resolved.get("leaders", [])
    ]

    execution = ExecutionConfig(**resolved.get("execution", {}))
    risk = RiskConfig(**resolved.get("risk", {}))

    tg_raw = resolved.get("notifications", {}).get("telegram", {})
    telegram = TelegramConfig(**tg_raw)

    logging_cfg = LoggingConfig(**resolved.get("logging", {}))
    health_database = HealthDatabaseConfig(**resolved.get("health_database", {}))

    return Config(
        polling=polling,
        binance_web=binance_web,
        binance_source=binance_source,
        leaders=leaders,
        execution=execution,
        risk=risk,
        notifications_telegram=telegram,
        logging=logging_cfg,
        health_database=health_database,
    )
