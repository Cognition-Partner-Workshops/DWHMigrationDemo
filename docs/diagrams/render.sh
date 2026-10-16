#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

CHROME="${PUPPETEER_EXECUTABLE_PATH:-}"
if [[ -z "$CHROME" ]]; then
  for candidate in google-chrome chromium chromium-browser; do
    CHROME="$(command -v "$candidate" || true)"
    if [[ -n "$CHROME" ]]; then
      break
    fi
  done
fi
if [[ -z "$CHROME" ]]; then
  printf 'Chrome was not found; set PUPPETEER_EXECUTABLE_PATH.\n' >&2
  exit 1
fi

PUPPETEER_CONFIG="$(mktemp)"
trap 'rm -f "$PUPPETEER_CONFIG"' EXIT
python3 -c 'import json, sys; json.dump({"executablePath": sys.argv[2]}, open(sys.argv[1], "w"))' \
  "$PUPPETEER_CONFIG" "$CHROME"

for diagram in architecture components deployment multi-agent-flow sequence; do
  npx --yes @mermaid-js/mermaid-cli@11.4.2 mmdc \
    --puppeteerConfigFile "$PUPPETEER_CONFIG" \
    --input "$SCRIPT_DIR/$diagram.mmd" \
    --output "$SCRIPT_DIR/$diagram.png" \
    --backgroundColor white \
    --width 1568 \
    --quiet
done
