#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${1:?Usage: nightly-review.sh /path/to/repository}"

export WPHEKA_CODERABBIT_MODE="${WPHEKA_CODERABBIT_MODE:-uncommitted}"

exec "$ROOT/bin/wpheka-quality" --repo "$REPO"
