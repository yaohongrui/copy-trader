import unittest

from src.binance_session import HybridBinancePositionSource
from src.config import BinanceBrowserConfig, BinanceSessionConfig, BinanceWebConfig
from src.poller import PollAuthError


class FakePoller:
    def __init__(self, responses):
        self.responses = list(responses)
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def fetch_positions(self, portfolio_id):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def reload(self, cfg):
        pass


class FakeKeeper:
    def __init__(self):
        self.refreshes = []

    async def refresh(self, portfolio_id=None):
        self.refreshes.append(portfolio_id)

    async def start(self):
        pass

    async def stop(self):
        pass


class HybridPositionSourceTests(unittest.IsolatedAsyncioTestCase):
    def _source(self, responses):
        source = HybridBinancePositionSource(
            FakePoller(responses), BinanceBrowserConfig(), BinanceSessionConfig(), "default"
        )
        source._keeper = FakeKeeper()
        return source

    async def test_uses_http_without_starting_browser_refresh(self):
        source = self._source([["position"]])

        result = await source.fetch_positions("leader-1")

        self.assertEqual(result, ["position"])
        self.assertEqual(source._keeper.refreshes, [])

    async def test_start_refreshes_http_auth_before_polling(self):
        source = self._source([])

        await source.start()

        self.assertTrue(source._poller.started)
        self.assertEqual(source._keeper.refreshes, [None])

    async def test_start_keeps_http_source_available_when_refresh_fails(self):
        source = self._source([])

        async def fail_refresh(portfolio_id=None):
            raise RuntimeError("browser unavailable")

        source._keeper.refresh = fail_refresh
        await source.start()

        self.assertTrue(source._poller.started)

    async def test_refreshes_browser_then_retries_http_auth_failure(self):
        source = self._source([PollAuthError("expired"), ["position"]])

        result = await source.fetch_positions("leader-1")

        self.assertEqual(result, ["position"])
        self.assertEqual(source._keeper.refreshes, ["leader-1"])


if __name__ == "__main__":
    unittest.main()
