#!/usr/bin/env bash
# Shared default job-count detection for the local gate scripts.
# Sourced by check-quick.sh and check-quick-backend.sh so the parallelism
# policy lives in exactly one place (per-lane env vars override; CI 4-vCPU
# runners are unaffected).
#
# detect_gate_default_jobs is the legacy conservative baseline. The gate
# scripts call detect_gate_default_jobs_worktree_aware instead, which divides
# the machine budget across the gates actually running: N concurrent gates
# (machine-wide slot count, scripts/gate-queue.sh) each get (cores-2)/N
# workers, clamped to [2, 8]. With no queue visibility (stubbed git, no
# common dir) it falls back to the sibling-lock probe: min(4, cores) while a
# sibling worktree runs a gate, cores-2 otherwise.

# count_live_gate_slots lives in gate-queue.sh; source it by this file's own
# location so any caller (check-quick.sh, check-quick-backend.sh, standalone
# sourcing) sees the machine-wide slot count.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gate-queue.sh"

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

# Machine budget split across concurrent gates. N = live machine-wide gate
# slots (includes this gate's own slot when queued through check-quick.sh),
# so 1 gate on 10 cores -> 8 workers, 2 gates -> 4 each, 4 gates -> 2 each.
# The floor of 2 keeps xdist functional on small machines; the cap of 8 keeps
# one lone gate from oversubscribing a big box.
_detect_gate_jobs_for_slots() {
  local slots="$1"
  local cores jobs
  cores="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
  [[ "$slots" -lt 1 ]] && slots=1
  jobs=$(( (cores - 2) / slots ))
  [[ "$jobs" -lt 2 ]] && jobs=2
  [[ "$jobs" -gt 8 ]] && jobs=8
  echo "$jobs"
}

# Default job count with worktree awareness. Prefer the machine-wide slot
# count (exact, includes this gate); when the queue is invisible fall back to
# the sibling-lock probe. Per-lane env overrides keep precedence.
detect_gate_default_jobs_worktree_aware() {
  local slots
  slots="$(count_live_gate_slots 2>/dev/null || echo 0)"
  if [[ "$slots" =~ ^[0-9]+$ && "$slots" -ge 1 ]]; then
    _detect_gate_jobs_for_slots "$slots"
    return
  fi
  if other_worktree_gate_running; then
    detect_gate_default_jobs
  else
    _detect_gate_jobs_for_slots 1
  fi
}
