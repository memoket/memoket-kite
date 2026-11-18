#!/usr/bin/env bash
set -euo pipefail
dir=$(cd "$(dirname "$0")" && pwd -P)
chrome="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT
dom=$("$chrome" --headless --disable-gpu --disable-sync --disable-background-networking \
  --no-first-run --no-default-browser-check --user-data-dir="$profile" --dump-dom \
  "file://$dir/reply_fixture.html" 2>"$profile/errors")
if [[ "$dom" != *'<output id="status">PASSED 21</output>'* ]]; then
    printf '%s\n' "$dom" | rg '<output'
    exit 1
fi
printf '%s\n' '21 browser assertions passed'
