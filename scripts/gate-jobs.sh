#!/usr/bin/env bash
# Shared default job-count detection for the local gate scripts.
# Sourced by check-quick.sh and check-quick-backend.sh so the min(4, cores)
# policy lives in exactly one place (per-lane env vars override; CI 4-vCPU
# runners are unaffected).
detect_gate_default_jobs() {
  local jobs
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
  if [[ "$jobs" -gt 4 ]]; then
    jobs=4
  fi
  echo "$jobs"
}
