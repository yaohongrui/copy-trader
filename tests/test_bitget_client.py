import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from src.bitget_client import BitgetAPIError, BitgetClient
from src.bitget_account import BitgetAccount


class BitgetQuantityTests(unittest.TestCase):
    def setUp(self):
        self.instrument = {
            "quantityMultiplier": "0.1",
            "minOrderQty": "1",
        }

    def test_quantity_is_rounded_down_to_exchange_step(self):
        self.assertEqual(
            BitgetClient.format_quantity(Decimal("12.39"), self.instrument),
            "12.3",
        )

    def test_quantity_below_minimum_is_rejected(self):
        with self.assertRaises(BitgetAPIError):
            BitgetClient.format_quantity(Decimal("0.99"), self.instrument)

    def test_round_to_step_rounds_down_to_exchange_step(self):
        self.assertEqual(
            BitgetClient.round_to_step(Decimal("1.23455"), {
                "quantityMultiplier": "0.01",
            }),
            Decimal("1.23"),
        )

    def test_round_to_step_returns_zero_below_step(self):
        self.assertEqual(
            BitgetClient.round_to_step(Decimal("0.005"), {
                "quantityMultiplier": "0.01",
            }),
            Decimal("0"),
        )


class BitgetOrderPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_way_market_order_omits_position_side(self):
        client = BitgetClient("key", "secret", "passphrase")
        client.get_instrument = AsyncMock(return_value={
            "quantityMultiplier": "0.001",
            "minOrderQty": "0.001",
        })
        client._request = AsyncMock(return_value={"orderId": "123"})

        await client.place_market_order(
            symbol="BTCUSDT",
            qty=Decimal("0.0123"),
            side="buy",
            reduce_only=False,
            client_oid="copy-test",
        )

        self.assertEqual(
            client._request.await_args.kwargs["body"],
            {
                "category": "USDT-FUTURES",
                "symbol": "BTCUSDT",
                "qty": "0.012",
                "side": "buy",
                "orderType": "market",
                "reduceOnly": "no",
                "marginMode": "crossed",
                "clientOid": "copy-test",
            },
        )

    async def test_positions_accepts_uta_position_list_wrapper(self):
        client = BitgetClient("key", "secret", "passphrase")
        expected = [{"symbol": "SPCXUSDT", "total": "2.74"}]
        client._request = AsyncMock(return_value={"positionList": expected})

        self.assertEqual(await client.get_positions(), expected)

    async def test_account_normalizes_entry_pnl_and_roi(self):
        account = BitgetAccount("key", "secret", "passphrase")
        account.client.get_positions = AsyncMock(return_value=[{
            "symbol": "SPCXUSDT", "total": "2.74", "holdSide": "long",
            "openPrice": "108.97", "markPrice": "110", "unrealizedPL": "2.82",
            "marginSize": "5.96",
        }])

        position = (await account.get_positions())[0]
        self.assertEqual(position["entry_price"], "108.97")
        self.assertEqual(position["unrealized_pnl"], "2.82")
        self.assertEqual(position["roi_pct"], str(Decimal("2.82") / Decimal("5.96") * 100))

    async def test_account_skips_zero_aliases_in_uta_position(self):
        account = BitgetAccount("key", "secret", "passphrase")
        account.client.get_positions = AsyncMock(return_value=[{
            "symbol": "SPCXUSDT", "total": "2.74", "holdSide": "long",
            "openPriceAvg": "0", "openPrice": "108.97",
            "markPrice": "108.62", "unrealizedPL": "0",
            "unrealizedPnl": "-0.9595", "marginSize": "0",
            "positionMargin": "5.96",
        }])

        position = (await account.get_positions())[0]
        self.assertEqual(position["entry_price"], "108.97")
        self.assertEqual(position["mark_price"], "108.62")
        self.assertEqual(position["unrealized_pnl"], "-0.9595")
        self.assertEqual(position["margin"], "5.96")
        self.assertEqual(
            position["roi_pct"],
            str(Decimal("-0.9595") / Decimal("5.96") * 100),
        )

    async def test_account_uses_official_uta_position_field_names(self):
        account = BitgetAccount("key", "secret", "passphrase")
        account.client.get_positions = AsyncMock(return_value=[{
            "symbol": "BTCUSDT", "total": "0.0023", "posSide": "long",
            "avgPrice": "63100", "markPrice": "63096.6",
            "unrealisedPnl": "-0.00782", "positionBalance": "2.9026",
            "profitRate": "-0.002694",
        }])

        position = (await account.get_positions())[0]
        self.assertEqual(position["entry_price"], "63100")
        self.assertEqual(position["mark_price"], "63096.6")
        self.assertEqual(position["unrealized_pnl"], "-0.00782")
        self.assertEqual(position["margin"], "2.9026")
        self.assertEqual(position["roi_pct"], "-0.269400")


if __name__ == "__main__":
    unittest.main()
