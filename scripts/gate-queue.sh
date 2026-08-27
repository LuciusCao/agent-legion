#!/usr/bin/env bash
# Machine-wide gate concurrency limiter, shared across every worktree of the
# repository through the git common directory (the one path all worktrees
# share on a host). check-quick.sh acquires a slot before running lanes and
# releases it on exit; a gate whose slots are all taken waits and prints the
# current holders, so N agents firing quality gates on one machine queue up
# instead of thrashing it (observed: 4 parallel quick gates stretched the
# last one to ~1h; each gate alone takes ~6min).
#
# Why slots and not a load-average probe: load is ambiguous (an unrelated
# build looks identical to a sibling gate) and non-actionable (how long to
# wait?). A slot count is exact, cheap, and gives a stable answer to "how
# many gates may run now" — the same design as the per-worktree
# .quick-gate.lock, lifted one level to the machine.
#
# Slot file: <git-common-dir>/gate-slots/gate-<pid>-<nanos>, holding pid /
# worktree / start time. A slot whose pid is dead is reclaimed on sight
# (crashed or SIGKILLed gate); AGENT_LEGION_MAX_PARALLEL_GATES (default 2)
# caps concurrent gates, 0 disables the queue entirely.
#
# Re-entrancy: check-quick.sh exports AGENT_LEGION_GATE_SLOT_HELD=1 while it
# holds a slot; a nested invocation (none today, insurance for check.sh-style
# wrappers) inherits the parent's slot instead of waiting on it.

gate_slot_dir() {
  local dir
  dir="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
  [[ -z "$dir" ]] && return 1
  # Resolve relative to the current worktree root when git answers relatively.
  if [[ "$dir" != /* ]]; then
    dir="$(git rev-parse --show-toplevel 2>/dev/null)/$dir"
  fi
  [[ -d "$dir" ]] || return 1
  printf '%s\n' "$dir/gate-slots"
}

# Live (pid still running) slot count, excluding nothing — callers decide
# whether to subtract their own slot. Any parse trouble reads as 0: the
# worker-division fallback then applies the conservative legacy default.
count_live_gate_slots() {
  local dir slot pid count=0
  dir="$(gate_slot_dir)" || { echo 0; return; }
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    pid="$(head -n1 "$slot" 2>/dev/null)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && count=$((count + 1))
  done
  echo "$count"
}

_reclaim_stale_gate_slots() {
  local dir slot pid
  dir="$(gate_slot_dir)" || return 0
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    pid="$(head -n1 "$slot" 2>/dev/null)"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$slot"
    fi
  done
}

# A pid whose process exited stays kill-0-alive as a zombie until its parent
# reaps it (bash reaps its own children; a wrapper that forgets to wait does
# not). Slot age bounds that hole: a slot older than
# AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS (default 7200, ~the longest sane
# full gate) is stale regardless of its pid.
_reclaim_aged_gate_slots() {
  local dir slot now mtime age
  local max_age="${AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS:-7200}"
  [[ "$max_age" =~ ^[0-9]+$ ]] || max_age=7200
  [[ "$max_age" -gt 0 ]] || return 0
  dir="$(gate_slot_dir)" || return 0
  now="$(date +%s)"
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    mtime="$(stat -f %m "$slot" 2>/dev/null || stat -c %Y "$slot" 2>/dev/null || echo "$now")"
    age=$((now - mtime))
    [[ "$age" -gt "$max_age" ]] && rm -rf "$slot"
  done
}

release_gate_slot() {
  [[ -n "${AGENT_LEGION_GATE_SLOT_FILE:-}" ]] || return 0
  rm -rf "$AGENT_LEGION_GATE_SLOT_FILE"
  unset AGENT_LEGION_GATE_SLOT_FILE
  unset AGENT_LEGION_GATE_SLOT_HELD
}

# Acquire one gate slot, waiting while all slots are held. Prints holder info
# on entry and every AGENT_LEGION_GATE_POLL_SECONDS (default 5) while waiting.
# Exports AGENT_LEGION_GATE_SLOT_FILE / _HELD on success.
acquire_gate_slot() {
  local max="${AGENT_LEGION_MAX_PARALLEL_GATES:-2}"
  [[ "$max" =~ ^[0-9]+$ ]] || max=2
  if [[ "$max" -eq 0 ]]; then
    # Queue disabled by explicit override: behave as an always-free machine.
    export AGENT_LEGION_GATE_SLOT_HELD=1
    return 0
  fi
  # Nested invocation (a wrapper already holding a slot): reuse it.
  if [[ "${AGENT_LEGION_GATE_SLOT_HELD:-}" == "1" ]]; then
    return 0
  fi
  local dir
  dir="$(gate_slot_dir)" || {
    # No usable git common dir (e.g. stubbed git in fixture repos): the queue
    # cannot coordinate anything, so proceed unlocked rather than fail.
    export AGENT_LEGION_GATE_SLOT_HELD=1
    return 0
  }
  mkdir -p "$dir"
  _reclaim_stale_gate_slots
  _reclaim_aged_gate_slots
  local poll="${AGENT_LEGION_GATE_POLL_SECONDS:-5}"
  local waited=0 announced=0 slot_file holders
  while true; do
    if [[ "$(count_live_gate_slots)" -lt "$max" ]]; then
      slot_file="$dir/gate-$$-$(date +%s%N 2>/dev/null || date +%s)"
      printf '%s\n%s\n%s\n' "$$" "$(pwd)" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$slot_file"
      export AGENT_LEGION_GATE_SLOT_FILE="$slot_file"
      export AGENT_LEGION_GATE_SLOT_HELD=1
      return 0
    fi
    if [[ "$announced" -eq 0 ]]; then
      holders="$(_describe_gate_slot_holders "$dir")"
      echo "Machine gate queue full (${max} concurrent max); waiting for a slot (holders: ${holders:-unknown})..." >&2
      announced=1
    elif (( waited > 0 )) && (( waited % 30 == 0 )); then
      holders="$(_describe_gate_slot_holders "$dir")"
      echo "Still waiting for a gate slot (${waited}s elapsed; holders: ${holders:-unknown})..." >&2
    fi
    sleep "$poll"
    waited=$((waited + poll))
    _reclaim_stale_gate_slots
    _reclaim_aged_gate_slots
  done
}

_describe_gate_slot_holders() {
  local dir="$1" slot pid wt line2
  local -a names=()
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    pid="$(head -n1 "$slot" 2>/dev/null)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || continue
    line2="$(sed -n '2p' "$slot" 2>/dev/null)"
    names+=("$(basename "${line2:-unknown}" 2>/dev/null || echo unknown)(pid ${pid})")
  done
  local IFS=','
  echo "${names[*]}"
}
