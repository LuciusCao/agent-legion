#!/usr/bin/env bash
# Machine-wide gate concurrency limiter, shared across every worktree of the
# repository through the git common directory (the one path all worktrees
# share on a host). check-quick.sh acquires a slot before running lanes and
# releases it on exit; a gate whose slots are all taken waits and prints the
# current holders, so N agents firing quality gates on one machine queue up
# instead of thrashing it (observed: 4 parallel quick gates stretched the
# last one to ~1h; each gate alone takes ~6min).
#
# The cap defaults to 1 (serialized). An earlier default of 2 still
# oversubscribed the box — each gate fans out into parallel backend/frontend/
# rust lanes, so 2 gates meant ~2x the machine in jobs, and the losers were
# the timing-sensitive tests that then flaked on timeouts (reruns and manual
# re-runs cost more than the queue wait they avoided). Serialized, every gate
# runs at the full machine budget and finishes at lone-gate speed.
#
# Why slots and not a load-average probe: load is ambiguous (an unrelated
# build looks identical to a sibling gate) and non-actionable (how long to
# wait?). A slot count is exact, cheap, and gives a stable answer to "how
# many gates may run now" — the same design as the per-worktree
# .quick-gate.lock, lifted one level to the machine.
#
# Slot file: <git-common-dir>/gate-slots/gate-<pid>-<nanos>, holding pid /
# worktree / start time. A slot whose pid is dead is reclaimed on sight
# (crashed or SIGKILLed gate); AGENT_LEGION_MAX_PARALLEL_GATES (default 1,
# serialized — see above) caps concurrent gates, 0 disables the queue
# entirely.
#
# Re-entrancy: check-quick.sh exports AGENT_LEGION_GATE_SLOT_HELD=1 while it
# holds a slot; a nested invocation (none today, insurance for check.sh-style
# wrappers) inherits the parent's slot instead of waiting on it.
#
# Vanishing slots: a contender that yields deletes the slot file it just
# created, so any slot globbed by another process can disappear before it is
# read. Every slot read therefore treats ENOENT as "slot gone" (skip it / do
# not count it / fall back on mtime) rather than an error — under the
# caller's set -e, a bare failed read would kill the whole gate (issue #488:
# a queued pre-push lost its 42-minute wait to exactly that).

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
    # Vanished between the glob and this read (a yielding contender removed
    # its own slot): it does not count — its owner conceded, not holds.
    pid="$(head -n1 "$slot" 2>/dev/null)" || continue
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      count=$((count + 1))
    fi
  done
  # echo ends the function with status 0 even when the last loop iteration
  # took the false branch (a bare [[ ]] && chain would surface as status 1
  # under the caller's set -e).
  echo "$count"
}

_reclaim_stale_gate_slots() {
  local dir slot pid
  dir="$(gate_slot_dir)" || return 0
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    # Vanished between the glob and this read: already gone, nothing to
    # reclaim — the yielding contender deleted its own slot.
    pid="$(head -n1 "$slot" 2>/dev/null)" || continue
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$slot"
    fi
  done
  return 0
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
    # A slot that vanished mid-read yields the fallback (= now): age 0, not
    # reclaimed this round — _slot_mtime never fails on a vanished slot.
    mtime="$(_slot_mtime "$slot" "$now")"
    age=$((now - mtime))
    if [[ "$age" -gt "$max_age" ]]; then
      rm -rf "$slot"
    fi
  done
  return 0
}

# File mtime as epoch seconds, platform-portable. BSD/macOS stat needs
# `-f %m`; GNU/Linux `stat -f` is the filesystem-status switch that would
# swallow the file argument as part of the format string, so try one syntax
# then the other. Each try is a single captured call (stderr swallowed, a
# failure just falls through) — never a probe-then-query pair: a slot file
# can vanish between the two (a yielding contender removed it after our
# glob), and that must read as "no mtime" (the caller's fallback: age 0, not
# reclaimed, re-checked next poll), never as a failed command substitution
# that set -e escalates into killing the whole gate (issue #488). Capturing
# also keeps a failed chain from leaking output into the age arithmetic.
_slot_mtime() {
  local file="$1" fallback="$2" mtime
  if mtime="$(stat -f %m "$file" 2>/dev/null)"; then
    echo "$mtime"
  elif mtime="$(stat -c %Y "$file" 2>/dev/null)"; then
    echo "$mtime"
  else
    echo "$fallback"
  fi
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
  local max="${AGENT_LEGION_MAX_PARALLEL_GATES:-1}"
  [[ "$max" =~ ^[0-9]+$ ]] || max=1
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
  local poll="${AGENT_LEGION_GATE_POLL_SECONDS:-5}"
  local waited=0 announced=0 slot_file holders
  while true; do
    if _try_acquire_gate_slot "$dir" "$max"; then
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

# Atomic admission without a mutex: every contender first creates its own
# slot file (creation is atomic; the pid lands in the same write), then
# counts live slots INCLUDING its own and yields (deletes its file) when the
# cap is exceeded. A start burst can briefly overshoot by the number of
# racers and converges to the cap within one poll interval — no coordination
# primitive to wedge, a crashed contender's file reclaims as a stale slot
# like any other, and the overshoot window (milliseconds) is irrelevant
# next to multi-minute gates. Yielding contenders back off a random sub-second
# delay so a synchronized retry burst does not re-collide.
_try_acquire_gate_slot() {
  local dir="$1" max="$2" slot_file
  _reclaim_stale_gate_slots
  _reclaim_aged_gate_slots
  slot_file="$dir/gate-$$-$(date +%s%N 2>/dev/null || date +%s)"
  printf '%s\n%s\n%s\n' "$$" "$(pwd)" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$slot_file"
  if [[ "$(count_live_gate_slots)" -le "$max" ]]; then
    export AGENT_LEGION_GATE_SLOT_FILE="$slot_file"
    export AGENT_LEGION_GATE_SLOT_HELD=1
    return 0
  fi
  rm -rf "$slot_file"
  # Jittered backoff: 0-0.5s, seeded from the nanosecond clock.
  sleep "0.$(printf '%03d' $(( $(date +%s%N 2>/dev/null | tail -c 4) % 500 )))" 2>/dev/null || true
  return 1
}

_describe_gate_slot_holders() {
  local dir="$1" slot pid wt line2
  local -a names=()
  for slot in "$dir"/gate-*; do
    [[ -f "$slot" ]] || continue
    # Vanished mid-description (a yielding contender removed it): no longer a
    # holder — skip it; a failed read must never kill the announcement.
    pid="$(head -n1 "$slot" 2>/dev/null)" || continue
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || continue
    line2="$(sed -n '2p' "$slot" 2>/dev/null)" || continue
    names+=("$(basename "${line2:-unknown}" 2>/dev/null || echo unknown)(pid ${pid})")
  done
  local IFS=','
  echo "${names[*]}"
}
