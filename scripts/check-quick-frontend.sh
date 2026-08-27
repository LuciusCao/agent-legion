#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/frontend"

case "${FRONTEND_TEST_MODE:-test}" in
  test) test_command="test" ;;
  coverage) test_command="test:coverage" ;;
  related) test_command="test" ;;
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

# Frontend files changed relative to the merge base (uncommitted edits +
# committed branch work), used by FRONTEND_TEST_MODE=related. Both sources and
# test files are selected: a direct test edit must always run that test, and
# `vitest related` treats a test file as its own related test. Paths are
# normalized to frontend-relative form for the vitest CLI. Fails open to
# "no files" (vitest exits 0 with no tests) only when git itself fails.
frontend_changed_sources() {
  local base
  base="$(git merge-base HEAD develop 2>/dev/null || git merge-base HEAD origin/develop 2>/dev/null || true)"
  {
    git status --porcelain=v1 --untracked-files=all -- . 2>/dev/null \
      | sed -e 's/^...//' -e 's/.* -> //' || true
    if [[ -n "$base" ]]; then
      git diff --name-only "$base" HEAD -- . 2>/dev/null || true
    fi
  } | sort -u | sed 's|^frontend/||'
}

run_related_tests() {
  # Affected-test selection for the agent inner loop: `vitest related`
  # resolves the module graph from each changed source file and runs only the
  # tests that import it (transitively). The full suite stays the CI
  # boundary; a `vitest related` pass is not a full-gate pass.
  local sources
  sources="$(frontend_changed_sources)"
  if [[ -z "$sources" ]]; then
    echo "=== Frontend Tests (related) ==="
    echo "No changed frontend source files; nothing to select."
    return 0
  fi
  local file_count
  file_count="$(printf '%s\n' "$sources" | wc -l | tr -d ' ')"
  echo "=== Frontend Tests (related: ${file_count} changed source file(s)) ==="
  local -a vitest_args=(related)
  while IFS= read -r src_file; do
    vitest_args+=("$src_file")
  done <<<"$sources"
  vitest_args+=(--run --maxWorkers="${AGENT_LEGION_FRONTEND_TEST_WORKERS:-4}")
  if [[ -n "${FRONTEND_TEST_PROJECT:-}" ]]; then
    vitest_args+=(--project "$FRONTEND_TEST_PROJECT")
  fi
  npx vitest "${vitest_args[@]}"
}

run_tests() {
  if [[ "$test_command" == "test" && "${FRONTEND_TEST_MODE:-test}" == "related" ]]; then
    run_related_tests
    return
  fi
  echo "=== Frontend Tests (${test_command}) ==="
  vitest_args=()
  # Vitest defaults to one worker thread per core, which oversubscribes
  # machines running multiple worktrees (node at 500%+ CPU). Cap the default;
  # CI runners (4 vCPU) are unaffected. AGENT_LEGION_FRONTEND_TEST_WORKERS
  # overrides.
  vitest_args+=(--maxWorkers="${AGENT_LEGION_FRONTEND_TEST_WORKERS:-4}")
  # FRONTEND_TEST_PROJECT selects a single Vitest project (logic/component)
  # so CI can shard the two environments into parallel jobs.
  if [[ -n "${FRONTEND_TEST_PROJECT:-}" ]]; then
    vitest_args+=(--project "$FRONTEND_TEST_PROJECT")
  fi
  # FRONTEND_COVERAGE_BLOB_DIR marks a coverage shard: emit a blob report
  # (which embeds the raw V8 coverage) for a downstream merge job, keep the
  # local coverage report cheap (text only), and defer threshold + inventory
  # enforcement to the merged run (a shard's partial coverage cannot meet
  # the global thresholds on its own).
  if [[ -n "${FRONTEND_COVERAGE_BLOB_DIR:-}" ]]; then
    mkdir -p "$FRONTEND_COVERAGE_BLOB_DIR"
    vitest_args+=(
      --reporter=blob
      --outputFile.blob="$FRONTEND_COVERAGE_BLOB_DIR/vitest-blob-${FRONTEND_TEST_PROJECT:-all}.json"
      --coverage.reporter=text
      --coverage.thresholds.lines=0
      --coverage.thresholds.functions=0
      --coverage.thresholds.branches=0
      --coverage.thresholds.statements=0
    )
  fi
  if [[ -n "${AGENT_LEGION_TEST_RESULTS_DIR:-}" ]]; then
    mkdir -p "$AGENT_LEGION_TEST_RESULTS_DIR"
    vitest_args+=(
      --reporter=default
      --reporter=junit
      --outputFile.junit="$AGENT_LEGION_TEST_RESULTS_DIR/vitest-junit.xml"
      --reporter=json
      --outputFile.json="$AGENT_LEGION_TEST_RESULTS_DIR/vitest-results.json"
    )
  fi
  if (( ${#vitest_args[@]} > 0 )); then
    npm run "$test_command" -- "${vitest_args[@]}"
  else
    npm run "$test_command"
  fi
  if [[ "$test_command" == "test:coverage" && -z "${FRONTEND_COVERAGE_BLOB_DIR:-}" ]]; then
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
