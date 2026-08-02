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
  # api:check boots the full backend app (create_app) to export the OpenAPI
  # schema. The local quick gate hoists it into a sequential integration step
  # (FRONTEND_API_CHECK=0) so the parallel lanes stay light; CI calls this
  # script directly and keeps the default inline behavior.
  if [[ "${FRONTEND_API_CHECK:-1}" == "1" ]]; then
    echo "=== Generated API Contract ==="
    npm run api:check
  fi

  echo "=== Frontend Format ==="
  npm run format:check

  echo "=== Frontend Lint ==="
  npm run lint

  echo "=== Frontend Type Check ==="
  npm run typecheck
}

run_tests() {
  echo "=== Frontend Tests (${test_command}) ==="
  reporter_args=()
  if [[ -n "${AGENT_LEGION_TEST_RESULTS_DIR:-}" ]]; then
    mkdir -p "$AGENT_LEGION_TEST_RESULTS_DIR"
    reporter_args=(
      --reporter=default
      --reporter=junit
      --outputFile.junit="$AGENT_LEGION_TEST_RESULTS_DIR/vitest-junit.xml"
      --reporter=json
      --outputFile.json="$AGENT_LEGION_TEST_RESULTS_DIR/vitest-results.json"
    )
  fi
  if (( ${#reporter_args[@]} > 0 )); then
    npm run "$test_command" -- "${reporter_args[@]}"
  else
    npm run "$test_command"
  fi
  if [[ "$test_command" == "test:coverage" ]]; then
    npm run test:coverage-inventory
  fi
}

case "${FRONTEND_GATE_PHASE:-all}" in
  static) run_static_checks ;;
  api-contract)
    echo "=== Generated API Contract ==="
    npm run api:check
    ;;
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
