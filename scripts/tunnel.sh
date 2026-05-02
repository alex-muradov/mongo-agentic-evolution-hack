#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec cloudflared tunnel --config "$HERE/../cloudflared/config.yml" run
