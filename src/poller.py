import logging
import re
from time import time

import aiohttp

from src.config import BinanceWebConfig
from src.models import LeaderPosition
from src.position_source import PollAuthError, PollError, parse_leader_positions

logger = logging.getLogger(__name__)

BASE_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-data/positions"
COOKIE_EXPIRY_RE = re.compile(r"(?:^|;)\s*BNC_FV_KEY_EXPIRE=([^;]+)")
REQUIRED_COOKIE_FIELDS = ("p20t",)


def cookie_expiry_ms(cookie: str) -> int | None:
    """Return the browser fingerprint cookie expiry timestamp, if present."""
    match = COOKIE_EXPIRY_RE.search(cookie or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def cookie_expired(cookie: str, now_ms: int | None = None) -> bool:
    expiry_ms = cookie_expiry_ms(cookie)
    return expiry_ms is not None and expiry_ms < (now_ms or int(time() * 1000))


def missing_cookie_fields(cookie: str) -> list[str]:
    fields = {
        part.strip().split("=", 1)[0].lower()
        for part in (cookie or "").split(";")
        if "=" in part
    }
    return [field for field in REQUIRED_COOKIE_FIELDS if field.lower() not in fields]


def cookie_value(cookie: str, name: str) -> str:
    target = name.lower()
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() == target:
            return value
    return ""


def _build_headers(cfg: BinanceWebConfig, portfolio_id: str) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "BNC-Location": "CN",
        "BNC-Time-Zone": "Asia/Shanghai",
        "BNC-Level": "0",
        "BNC-UUID": cfg.bnc_uuid or cookie_value(cfg.cookie, "bnc-uuid"),
        "Cache-Control": "no-cache",
        "clienttype": "web",
        "Content-Type": "application/json",
        "csrftoken": cfg.csrf_token,
        "Device-Info": cfg.device_info,
        "FVIDEO-ID": cfg.fvideo_id or cookie_value(cfg.cookie, "BNC_FV_KEY"),
        "FVIDEO-Token": cfg.fvideo_token,
        "Lang": "zh-CN",
        "Pragma": "no-cache",
        "Referer": f"https://www.binance.com/zh-CN/copy-trading/lead-details/{portfolio_id}",
        "User-Agent": cfg.user_agent,
        "Cookie": cfg.cookie,
    }


class Poller:
    def __init__(self, cfg: BinanceWebConfig):
        self._cfg = cfg
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    def reload(self, cfg: BinanceWebConfig):
        """热重载：更新 Cookie/CSRF 等请求头配置。"""
        self._cfg = cfg
        logger.info("Poller config reloaded")

    @property
    def auth_config(self) -> BinanceWebConfig:
        """Return the current in-memory HTTP authentication settings."""
        return self._cfg

    async def fetch_positions(self, portfolio_id: str) -> list[LeaderPosition]:
        """Fetch leader positions.

        成功返回位置列表（可能为空）；失败抛出异常：
          - PollAuthError: Cookie 失效/鉴权失败
          - PollError: 网络或数据解析错误

        注意：网络错误绝不能返回空列表，否则会被当成"带单员已清仓"
        而触发批量平仓。
        """
        if not self._session:
            raise RuntimeError("Poller not started")

        headers = _build_headers(self._cfg, portfolio_id)
        url = f"{BASE_URL}?portfolioId={portfolio_id}"

        try:
            async with self._session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error("Poll failed for %s: %s", portfolio_id, e)
            raise PollError(f"Request failed for {portfolio_id}: {e}") from e

        if data.get("code") != "000000":
            logger.error("API error for %s: %s", portfolio_id, data.get("message"))
            raise PollAuthError(f"API error for {portfolio_id}: {data.get('message')}")

        try:
            return parse_leader_positions(data.get("data"), portfolio_id)
        except PollError as e:
            logger.error("Failed to parse positions for %s: %s", portfolio_id, e)
            raise
