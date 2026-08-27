#!/usr/bin/env bash
# Shared default job-count detection for the local gate scripts.
# Sourced by check-quick.sh and check-quick-backend.sh so the parallelism
# policy lives in exactly one place (per-lane env vars override; CI 4-vCPU
# runners are unaffected).
#
# detect_gate_default_jobs is the legacy conservative baseline. The gate
# scripts call detect_gate_default_jobs_worktree_aware instead: the polite
# min(4, cores) cap exists because several worktrees may run gates
# concurrently on one machine, and when no sibling worktree holds a quick
# gate there is spare CPU, so the default climbs to cores-2 (the headroom
# keeps the parallel backend/frontend lanes from starving each other).

detect_gate_default_jobs() {
  local jobs
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
  if [[ "$jobs" -gt 4 ]]; then
    jobs=4
  fi
  echo "$jobs"
}

# True when another worktree of this repository currently holds its quick-gate
# lock. The lock directory is created by scripts/check-quick.sh at gate start
# and removed on exit; a lock whose pid is no longer alive does not count
# (check-quick.sh reclaims stale locks itself). Worktree checkout paths come
# from `git worktree list --porcelain` — the git-common-dir only exposes
# per-worktree metadata directories, not the checkouts holding locks.
other_worktree_gate_running() {
  local self_worktree line worktree lock pid
  self_worktree="$(git rev-parse --show-toplevel 2>/dev/null)"
  if [[ -n "$self_worktree" ]]; then
    self_worktree="$(cd "$self_worktree" && pwd)"
  fi
  while IFS= read -r line; do
    [[ "$line" == worktree\ * ]] || continue
    worktree="${line#worktree }"
    [[ -z "$worktree" ]] && continue
    [[ -n "$self_worktree" && "$worktree" == "$self_worktree" ]] && continue
    lock="$worktree/.quick-gate.lock"
    [[ -d "$lock" ]] || continue
    [[ -f "$lock/pid" ]] || continue
    pid="$(cat "$lock/pid" 2>/dev/null)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && return 0
  done < <(git worktree list --porcelain 2>/dev/null)
  return 1
}

# Default job count with worktree awareness: min(4, cores) while a sibling
# worktree runs a gate, cores-2 (capped at 8, floored at 1) otherwise.
# Per-lane env overrides keep precedence.
detect_gate_default_jobs_worktree_aware() {
  local jobs cores
  if other_worktree_gate_running; then
    cores="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
    jobs="$cores"
    [[ "$jobs" -gt 4 ]] && jobs=4
  else
    cores="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
    jobs=$((cores > 3 ? cores - 2 : cores))
    [[ "$jobs" -lt 1 ]] && jobs=1
    [[ "$jobs" -gt 8 ]] && jobs=8
  fi
  echo "$jobs"
}
