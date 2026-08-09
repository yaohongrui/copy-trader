import tempfile
import unittest
from pathlib import Path

from src.update_binance_headers import main, parse_browser_request, update_config


class BrowserHeaderUpdateTests(unittest.TestCase):
    def test_parses_devtools_two_line_headers(self):
        values = parse_browser_request(
            "cookie\n"
            "p20t=value; other=value\n"
            "csrftoken\n"
            "csrf-value\n"
            "device-info\n"
            "device-value\n"
            "fvideo-token\n"
            "fvideo-value\n"
        )

        self.assertEqual(values["cookie"], "p20t=value; other=value")
        self.assertEqual(values["csrf_token"], "csrf-value")
        self.assertEqual(values["device_info"], "device-value")
        self.assertEqual(values["fvideo_token"], "fvideo-value")

    def test_updates_only_browser_fields_and_creates_backup(self):
        content = (
            "binance_web:\n"
            "  cookie: \"old-cookie\"\n"
            "  csrf_token: \"old-csrf\"\n"
            "  device_info: \"old-device\"\n"
            "  fvideo_token: \"old-fvideo\"\n"
            "other: unchanged\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(content, encoding="utf-8")

            backup_path = update_config(config_path, {
                "cookie": "new-cookie",
                "csrf_token": "new-csrf",
                "device_info": "new-device",
                "fvideo_token": "new-fvideo",
            })

            self.assertEqual(backup_path.read_text(encoding="utf-8"), content)
            updated = config_path.read_text(encoding="utf-8")
            self.assertIn('cookie: "new-cookie"', updated)
            self.assertIn('csrf_token: "new-csrf"', updated)
            self.assertIn('device_info: "new-device"', updated)
            self.assertIn('fvideo_token: "new-fvideo"', updated)
            self.assertIn("other: unchanged", updated)

    def test_dry_run_does_not_write_config(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.txt"
            request_path.write_text(
                "cookie: p20t=value\n"
                "csrftoken: csrf\n"
                "device-info: device\n"
                "fvideo-token: fvideo\n",
                encoding="utf-8",
            )

            self.assertEqual(main(["--input", str(request_path), "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
