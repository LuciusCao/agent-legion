#!/usr/bin/env bash
# Non-blocking dependency vulnerability audit: Python (uv.lock export + pip-audit)
# and frontend (npm audit). Requires network access; failures are reported, not fatal
# unless this script is run directly.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

status=0

echo "=== Python Dependency Audit (pip-audit) ==="
req_file="$(mktemp -t agent-legion-deps.XXXXXX)"
trap 'rm -f "$req_file"' EXIT
if UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv export --frozen --no-dev --format requirements-txt >"$req_file" 2>/dev/null; then
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uvx --from pip-audit pip-audit -r "$req_file" --progress-spinner off || status=1
else
  echo "dependency audit: uv export failed; skipping Python audit" >&2
  status=1
fi

echo "=== Frontend Dependency Audit (npm audit) ==="
if ! (cd frontend && npm audit --omit=dev --audit-level=high); then
  status=1
fi

exit "$status"
