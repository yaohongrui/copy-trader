"""Update Binance browser request fields from a copied Network request."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REQUIRED_HEADERS = {
    "cookie": "cookie",
    "csrftoken": "csrf_token",
    "device-info": "device_info",
    "fvideo-token": "fvideo_token",
}


def parse_browser_request(text: str) -> dict[str, str]:
    lines = text.splitlines()
    found: dict[str, str] = {}
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        match = re.match(r"^([A-Za-z0-9-]+)\s*:\s*(.+)$", line)
        if match:
            name, value = match.group(1).lower(), match.group(2).strip()
        elif line.lower() in REQUIRED_HEADERS and index + 1 < len(lines):
            name, value = line.lower(), lines[index + 1].strip()
        else:
            continue
        if name in REQUIRED_HEADERS and value:
            found[REQUIRED_HEADERS[name]] = value

    missing = sorted(set(REQUIRED_HEADERS.values()) - set(found))
    if missing:
        raise ValueError(f"Missing browser request fields: {', '.join(missing)}")
    return found


def update_config(config_path: Path, values: dict[str, str]) -> Path:
    original = config_path.read_text(encoding="utf-8")
    updated = original
    for key, value in values.items():
        pattern = re.compile(rf"^(  {re.escape(key)}:\s*).*$", re.MULTILINE)
        replacement = rf"\1{json.dumps(value, ensure_ascii=False)}"
        updated, count = pattern.subn(replacement, updated, count=1)
        if count != 1:
            raise ValueError(f"Could not find binance_web.{key} in {config_path}")

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    temp_path.write_text(updated, encoding="utf-8")
    temp_path.replace(config_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update Binance browser request fields from pasted DevTools request headers"
    )
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--input", help="Path to copied browser request text; omit to read stdin")
    parser.add_argument("--dry-run", action="store_true", help="Validate input without changing config")
    args = parser.parse_args(argv)

    text = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    if not text.strip():
        parser.error("No request text received")

    values = parse_browser_request(text)
    if args.dry_run:
        print(f"Valid request data: {', '.join(sorted(values))}")
        print("Dry run complete; config was not changed")
        return 0

    backup_path = update_config(Path(args.config), values)
    print(f"Updated {args.config}: {', '.join(sorted(values))}")
    print(f"Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
