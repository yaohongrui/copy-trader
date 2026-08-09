#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export DISPLAY="${DISPLAY:-:99}"
exec python3 -m src.binance_auth \
  --profile "${BINANCE_BROWSER_PROFILE:-data/binance-browser-profile}" login
