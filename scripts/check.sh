#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Quick Gate ==="
"$ROOT_DIR/scripts/check-quick.sh"

echo "=== Frontend Production Build ==="
cd "$ROOT_DIR/frontend"
npm run build
