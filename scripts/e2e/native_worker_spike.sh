#!/usr/bin/env bash
set -euo pipefail

UNIT_NAME=agent-legion-native-spike.service
INSTALL_ROOT=/opt/agent-legion-native-spike
STATE_DIR=/var/lib/agent-legion-native-spike
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SOURCE="$SCRIPT_DIR/native_worker_spike.service"

usage() {
  echo "usage: $0 verify | install --repo PATH | run | collect --output PATH" >&2
  exit 2
}

require_ci_root() {
  if [[ ${CI:-} != true || ${NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL:-} != 1 ]]; then
    echo "native spike systemd changes require CI=true and NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL=1" >&2
    exit 2
  fi
  if [[ $(id -u) -ne 0 ]]; then
    echo "native spike CI service installation must run as root" >&2
    exit 2
  fi
}

verify_unit() {
  bash -n "$SCRIPT_DIR/native_worker_spike.sh"
  systemd-analyze verify "$UNIT_SOURCE"
}

install_service() {
  local repo=$1
  require_ci_root
  [[ -f $repo/scripts/e2e/native_worker_spike.py && -x $repo/.venv/bin/python ]] || {
    echo "checkout with synced .venv is required: $repo" >&2
    exit 2
  }
  [[ -x $repo/data/bin/velites ]] || {
    echo "data/bin/velites must be built before service installation" >&2
    exit 2
  }
  if ! id agent-legion-spike >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$STATE_DIR" --shell /usr/sbin/nologin agent-legion-spike
  fi
  rm -rf "$INSTALL_ROOT"
  mkdir -p "$INSTALL_ROOT/repo" "$INSTALL_ROOT/repo/data"
  local path
  for path in .venv server worker shared workspace_libs workflow_nodes scripts config data/bin; do
    cp -a "$repo/$path" "$INSTALL_ROOT/repo/$path"
  done
  install -m 0644 "$UNIT_SOURCE" "/etc/systemd/system/$UNIT_NAME"
  systemd-analyze verify "/etc/systemd/system/$UNIT_NAME"
  mkdir -p "$STATE_DIR"
  chown agent-legion-spike:agent-legion-spike "$STATE_DIR"
  chmod 0700 "$STATE_DIR"
  rm -rf "$STATE_DIR/evidence" "$STATE_DIR/work" "$STATE_DIR/host-data"
  systemctl daemon-reload
}

run_service() {
  require_ci_root
  systemctl reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
  systemctl start --wait "$UNIT_NAME" &
  local start_wait_pid=$!
  local ready=$STATE_DIR/evidence/n0-ready.json
  for _ in {1..300}; do
    [[ -f $ready ]] && break
    if [[ $(systemctl is-active "$UNIT_NAME" || true) == failed ]]; then
      break
    fi
    sleep 0.1
  done
  mkdir -p "$STATE_DIR/evidence"
  systemctl show "$UNIT_NAME" \
    --property=MainPID --property=ControlGroup --property=User \
    --property=NoNewPrivileges --property=CapabilityBoundingSet \
    > "$STATE_DIR/evidence/systemd-runtime.txt"
  [[ -f $ready ]] || {
    echo "N0 service failed before its ready evidence" >&2
    return 1
  }
  local main_pid
  main_pid=$(systemctl show "$UNIT_NAME" --property=MainPID --value)
  "$INSTALL_ROOT/repo/.venv/bin/python" - "$ready" "$main_pid" <<'PY'
import json
import sys
from pathlib import Path
ready = json.loads(Path(sys.argv[1]).read_text())
if int(sys.argv[2]) <= 0 or ready["pid"] != int(sys.argv[2]):
    raise SystemExit("systemd MainPID does not own the N0 harness")
if not any(".service" in row for row in ready["cgroup"]):
    raise SystemExit("N0 harness is not in its systemd service cgroup")
PY
  wait "$start_wait_pid"
  [[ $(systemctl show "$UNIT_NAME" --property=Result --value) == success ]] || {
    echo "N0 native spike service failed; preserving evidence" >&2
    return 1
  }
}

collect_evidence() {
  local output=$1
  require_ci_root
  mkdir -p "$output"
  if [[ -d $STATE_DIR/evidence ]]; then
    cp -a "$STATE_DIR/evidence/." "$output/"
  fi
  journalctl -u "$UNIT_NAME" --no-pager > "$output/systemd-journal.log" || true
  chown -R "${SUDO_UID:-0}:${SUDO_GID:-0}" "$output"
  chmod -R a+rX "$output"
}

[[ $# -ge 1 ]] || usage
case $1 in
  verify)
    [[ $# -eq 1 ]] || usage
    verify_unit
    ;;
  install)
    [[ $# -eq 3 && $2 == --repo ]] || usage
    install_service "$3"
    ;;
  run)
    [[ $# -eq 1 ]] || usage
    run_service
    ;;
  collect)
    [[ $# -eq 3 && $2 == --output ]] || usage
    collect_evidence "$3"
    ;;
  *)
    usage
    ;;
esac
