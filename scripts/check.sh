#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check.$$}"
export COVERAGE_FILE
KEEP_COVERAGE=1
export KEEP_COVERAGE

cleanup_coverage() {
  rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*
}
trap cleanup_coverage EXIT

echo "=== Quick Gate ==="
FRONTEND_TEST_MODE=coverage "$ROOT_DIR/scripts/check-quick.sh"

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-legion-full.XXXXXX")"
cleanup_logs() {
  rm -rf "$log_dir"
}
trap 'cleanup_coverage; cleanup_logs' EXIT

full_log="$log_dir/backend-full.log"
build_log="$log_dir/frontend-build.log"

echo "=== Parallel Full Gate Extensions ==="
echo "Starting full backend evidence and frontend production bundle."
extensions_started_at=$SECONDS

(
  cd "$ROOT_DIR"
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q tests/full \
    -m full_gate --cov=server --cov-report= --cov-append
) >"$full_log" 2>&1 &
full_pid=$!
(
  cd "$ROOT_DIR/frontend"
  npm run build:bundle
) >"$build_log" 2>&1 &
build_pid=$!

set +e
wait "$full_pid"
full_status=$?
wait "$build_pid"
build_status=$?
set -e

echo "Parallel full extensions finished in $((SECONDS - extensions_started_at))s."
echo "=== Full Backend Lane Output ==="
cat "$full_log"
echo "=== Frontend Build Lane Output ==="
cat "$build_log"

if [[ "$full_status" -ne 0 || "$build_status" -ne 0 ]]; then
  echo "Parallel full gate extension failed: backend=$full_status build=$build_status" >&2
  exit 1
fi

echo "=== Combined Coverage Report ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run coverage report

echo "=== Dependency Vulnerability Audit (non-blocking) ==="
if ! "$ROOT_DIR/scripts/check-deps-audit.sh"; then
  echo "WARNING: dependency audit reported issues or could not complete (non-blocking)." >&2
fi
