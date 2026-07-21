#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/frontend"

case "${FRONTEND_TEST_MODE:-test}" in
  test) test_command="test" ;;
  coverage) test_command="test:coverage" ;;
  *)
    echo "Unsupported FRONTEND_TEST_MODE: ${FRONTEND_TEST_MODE}" >&2
    exit 2
    ;;
esac

run_static_checks() {
  echo "=== Generated API Contract ==="
  npm run api:check

  echo "=== Frontend Format ==="
  npm run format:check

  echo "=== Frontend Lint ==="
  npm run lint

  echo "=== Frontend Type Check ==="
  npm run typecheck
}

run_tests() {
  echo "=== Frontend Tests (${test_command}) ==="
  npm run "$test_command"
}

case "${FRONTEND_GATE_PHASE:-all}" in
  static) run_static_checks ;;
  test) run_tests ;;
  all)
    run_static_checks
    run_tests
    ;;
  *)
    echo "Unsupported FRONTEND_GATE_PHASE: ${FRONTEND_GATE_PHASE}" >&2
    exit 2
    ;;
esac
