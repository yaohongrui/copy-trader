"""Bitget Unified Trading Account (UTA) REST client.

Only Bitget's official v3 API is used.  All quantities exposed by this module
are base-coin quantities, never exchange contract counts.
"""
import base64
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from time import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)


class BitgetAPIError(RuntimeError):
    pass


class BitgetSymbolUnavailableError(BitgetAPIError):
    """The symbol is not tradable on Bitget (for example, it was removed)."""
    pass


class BitgetClient:
    BASE_URL = "https://api.bitget.com"
    CATEGORY = "USDT-FUTURES"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._session: aiohttp.ClientSession | None = None
        self._instruments: dict[str, dict[str, Any]] = {}

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        timestamp = str(int(time() * 1000))
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        signature = base64.b64encode(
            hmac.new(self._api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self._api_passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    async def _request(
        self, method: str, endpoint: str, *, params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None, private: bool = False,
    ) -> Any:
        if not self._session:
            raise RuntimeError("Bitget client not started")
        query = urlencode(params or {})
        path = f"{endpoint}?{query}" if query else endpoint
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = self._headers(method, path, payload) if private else {"Content-Type": "application/json"}
        url = f"{self.BASE_URL}{path}"
        try:
            async with self._session.request(
                method, url, headers=headers, data=payload or None,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                data = await response.json(content_type=None)
        except Exception as exc:
            raise BitgetAPIError(f"{method} {endpoint} request failed: {exc}") from exc

        if response.status != 200 or not isinstance(data, dict) or data.get("code") != "00000":
            message = data.get("msg", data) if isinstance(data, dict) else data
            if "symbol has been removed" in str(message).lower():
                raise BitgetSymbolUnavailableError(f"{method} {endpoint} failed: {message}")
            raise BitgetAPIError(f"{method} {endpoint} failed: {message}")
        return data.get("data")

    async def get_account_assets(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/account/assets", private=True)

    async def set_hold_mode(self, hold_mode: str = "one_way_mode"):
        await self._request(
            "POST", "/api/v3/account/set-hold-mode",
            body={"holdMode": hold_mode}, private=True,
        )

    async def get_positions(self) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", "/api/v3/position/current-position",
            params={"category": self.CATEGORY}, private=True,
        )
        if isinstance(data, list):
            return data
        # UTA returns the array directly for some accounts/API versions, but
        # other valid responses wrap it in an object.  Treating the wrapper as
        # an empty list made the health check report "positions: none" even
        # after a confirmed fill.
        if isinstance(data, dict):
            for key in ("positionList", "list"):
                positions = data.get(key)
                if isinstance(positions, list):
                    return positions
        return []

    async def get_order_info(
        self, *, order_id: str | None = None, client_oid: str | None = None,
    ) -> dict[str, Any]:
        if not order_id and not client_oid:
            raise ValueError("order_id or client_oid is required")
        params = {"orderId": order_id} if order_id else {"clientOid": client_oid}
        data = await self._request(
            "GET", "/api/v3/trade/order-info", params=params, private=True,
        )
        if not isinstance(data, dict):
            raise BitgetAPIError("Bitget returned no order details")
        return data

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        data = await self._request(
            "GET", "/api/v3/market/tickers",
            params={"category": self.CATEGORY, "symbol": symbol},
        )
        if not data:
            raise BitgetAPIError(f"Ticker not found for {symbol}")
        return data[0]

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._instruments:
            data = await self._request(
                "GET", "/api/v3/market/instruments",
                params={"category": self.CATEGORY, "symbol": symbol},
            )
            if not data:
                raise BitgetAPIError(f"Instrument not found for {symbol}")
            self._instruments[symbol] = data[0]
        return self._instruments[symbol]

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        instrument = await self.get_instrument(symbol)
        max_leverage = int(Decimal(str(instrument.get("maxLeverage", leverage))))
        applied = min(leverage, max_leverage)
        await self._request("POST", "/api/v3/account/set-leverage", body={
            "category": self.CATEGORY,
            "symbol": symbol,
            "marginMode": "crossed",
            "leverage": str(applied),
        }, private=True)
        return applied

    async def place_market_order(
        self, *, symbol: str, qty: Decimal, side: str, reduce_only: bool, client_oid: str,
    ) -> dict[str, Any]:
        # In UTA one-way mode, ``posSide`` must be omitted.  It is only valid
        # for hedge-mode orders (where it is ``long`` or ``short``); sending
        # the legacy ``net`` value makes Bitget reject the order.
        return await self._request("POST", "/api/v3/trade/place-order", body={
            "category": self.CATEGORY,
            "symbol": symbol,
            "qty": self.format_quantity(qty, await self.get_instrument(symbol)),
            "side": side,
            "orderType": "market",
            "reduceOnly": "yes" if reduce_only else "no",
            "marginMode": "crossed",
            "clientOid": client_oid,
        }, private=True)

    async def place_limit_order(
        self, *, symbol: str, qty: Decimal, side: str, price: Decimal,
        reduce_only: bool, client_oid: str,
    ) -> dict[str, Any]:
        instrument = await self.get_instrument(symbol)
        price_place = instrument.get("pricePlace")
        if price_place is not None:
            quantum = Decimal(1).scaleb(-int(price_place))
            price = (price // quantum) * quantum
        price_str = format(price, "f")
        return await self._request("POST", "/api/v3/trade/place-order", body={
            "category": self.CATEGORY,
            "symbol": symbol,
            "qty": self.format_quantity(qty, instrument),
            "price": price_str,
            "side": side,
            "orderType": "limit",
            "force": "gtc",
            "reduceOnly": "yes" if reduce_only else "no",
            "marginMode": "crossed",
            "clientOid": client_oid,
        }, private=True)

    async def cancel_order(self, *, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancel an outstanding UTA order by its exchange order ID."""
        return await self._request("POST", "/api/v3/trade/cancel-order", body={
            "category": self.CATEGORY,
            "symbol": symbol,
            "orderId": order_id,
        }, private=True)

    @staticmethod
    def round_to_step(qty: Decimal, instrument: dict[str, Any]) -> Decimal:
        step = Decimal(str(instrument["quantityMultiplier"]))
        return (qty // step) * step

    @staticmethod
    def format_quantity(qty: Decimal, instrument: dict[str, Any]) -> str:
        rounded = BitgetClient.round_to_step(qty, instrument)
        if rounded < Decimal(str(instrument["minOrderQty"])):
            raise BitgetAPIError(
                f"Quantity {rounded} is below Bitget minimum {instrument['minOrderQty']}"
            )
        return format(rounded, "f")
