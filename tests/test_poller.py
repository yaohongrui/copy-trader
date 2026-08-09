import unittest

from src.config import BinanceWebConfig
from src.poller import (
    _build_headers,
    cookie_expired,
    cookie_expiry_ms,
    missing_cookie_fields,
)


class CookieValidationTests(unittest.TestCase):
    def test_cookie_expiry_is_parsed_and_detected(self):
        cookie = "foo=bar; BNC_FV_KEY_EXPIRE=1700000000000; theme=dark"

        self.assertEqual(cookie_expiry_ms(cookie), 1700000000000)
        self.assertTrue(cookie_expired(cookie, now_ms=1700000000001))

    def test_cookie_without_expiry_is_not_marked_expired(self):
        self.assertIsNone(cookie_expiry_ms("foo=bar"))
        self.assertFalse(cookie_expired("foo=bar", now_ms=1700000000001))

    def test_malformed_expiry_is_ignored(self):
        self.assertIsNone(cookie_expiry_ms("BNC_FV_KEY_EXPIRE=not-a-timestamp"))
        self.assertFalse(cookie_expired("BNC_FV_KEY_EXPIRE=not-a-timestamp", now_ms=1700000000001))

    def test_p20t_is_the_required_cookie_field(self):
        self.assertEqual(missing_cookie_fields("foo=bar"), ["p20t"])
        self.assertEqual(missing_cookie_fields("foo=bar; p20t=value"), [])

    def test_request_headers_fall_back_to_cookie_fingerprint_values(self):
        cfg = BinanceWebConfig(cookie="bnc-uuid=uuid-1; BNC_FV_KEY=fvideo-1")

        headers = _build_headers(cfg, "portfolio")

        self.assertEqual(headers["BNC-UUID"], "uuid-1")
        self.assertEqual(headers["FVIDEO-ID"], "fvideo-1")


if __name__ == "__main__":
    unittest.main()
