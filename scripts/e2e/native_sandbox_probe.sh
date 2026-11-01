#!/usr/bin/env bash
set -euo pipefail

UNIT_NAME=agent-legion-native-sandbox-probe.service
INSTALL_ROOT=/opt/agent-legion-native-sandbox-probe
STATE_DIR=/var/lib/agent-legion-native-sandbox-probe
SERVICE_USER=agent-legion-sandbox-probe
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SOURCE="$SCRIPT_DIR/native_sandbox_probe.service"
HARNESS="$INSTALL_ROOT/repo/scripts/e2e/native_sandbox_probe.py"

usage() {
  echo "usage: $0 verify | install --repo PATH | run | collect --output PATH" >&2
  exit 2
}

require_ci_root() {
  if [[ ${CI:-} != true || ${NATIVE_SANDBOX_PROBE_ALLOW_SYSTEMD_INSTALL:-} != 1 ]]; then
    echo "native sandbox probe requires CI=true and NATIVE_SANDBOX_PROBE_ALLOW_SYSTEMD_INSTALL=1" >&2
    exit 2
  fi
  if [[ $(id -u) -ne 0 ]]; then
    echo "native sandbox probe service installation must run as root" >&2
    exit 2
  fi
}

verify_unit() {
  bash -n "$SCRIPT_DIR/native_sandbox_probe.sh"
  systemd-analyze verify "$UNIT_SOURCE"
}

run_harness() {
  local context=$1
  local cases=$2
  runuser -u "$SERVICE_USER" -- env \
    HOME="$STATE_DIR" USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
    /usr/bin/python3 "$HARNESS" \
    --state-dir "$STATE_DIR" --output-dir "$STATE_DIR/evidence" \
    --context "$context" --cases "$cases"
}

run_e4_case() {
  local case=$1
  local nnp=$2
  local cap_mode=$3
  local unit="agent-legion-${case}.service"
  local properties=(
    "--property=Type=oneshot"
    "--property=User=$SERVICE_USER"
    "--property=Group=$SERVICE_USER"
    "--property=WorkingDirectory=$STATE_DIR"
    "--property=Environment=HOME=$STATE_DIR"
    "--property=Environment=USER=$SERVICE_USER"
    "--property=Environment=LOGNAME=$SERVICE_USER"
    "--property=Environment=AGENT_LEGION_SKIP_DOTENV=1"
    "--property=StateDirectory=agent-legion-native-sandbox-probe"
    "--property=StateDirectoryMode=0700"
    "--property=NoNewPrivileges=$nnp"
    "--property=AmbientCapabilities="
    "--property=SecureBits=noroot noroot-locked no-setuid-fixup no-setuid-fixup-locked"
    "--property=UMask=0077"
    "--property=ProtectSystem=strict"
    "--property=ProtectHome=read-only"
    "--property=ReadWritePaths=$STATE_DIR"
    "--property=PrivateTmp=yes"
    "--property=PrivateDevices=yes"
    "--property=ProtectKernelTunables=yes"
    "--property=ProtectKernelModules=yes"
    "--property=ProtectKernelLogs=yes"
    "--property=ProtectControlGroups=yes"
    "--property=ProtectHostname=yes"
    "--property=ProtectClock=yes"
    "--property=RestrictSUIDSGID=yes"
    "--property=LockPersonality=yes"
    "--property=LimitNOFILE=65535"
    "--property=TimeoutStopSec=35s"
    "--property=KillMode=mixed"
  )
  if [[ $cap_mode == empty ]]; then
    properties+=("--property=CapabilityBoundingSet=")
  fi
  if ! systemd-run --quiet --wait --pipe --unit "$unit" "${properties[@]}" \
    /usr/bin/python3 "$HARNESS" \
    --state-dir "$STATE_DIR" --output-dir "$STATE_DIR/evidence" \
    --context "e4-nnp-${nnp}-cap-${cap_mode}" --cases "$case" \
    >> "$STATE_DIR/evidence/e4-systemd-launchers.log" 2>&1; then
    printf 'E4 transient launcher failed: %s\n' "$case" \
      >> "$STATE_DIR/evidence/e4-systemd-launchers.log"
  fi
}

install_service() {
  local repo=$1
  require_ci_root
  [[ -f $repo/scripts/e2e/native_sandbox_probe.py && -x $repo/data/bin/velites ]] || {
    echo "checkout with current data/bin/velites is required: $repo" >&2
    exit 2
  }
  local sha
  sha=$(git -C "$repo" rev-parse HEAD)
  if [[ -n ${GITHUB_SHA:-} && $sha != "$GITHUB_SHA" ]]; then
    echo "native probe checkout $sha does not match GITHUB_SHA $GITHUB_SHA" >&2
    exit 2
  fi
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --uid 999 --user-group --home-dir "$STATE_DIR" \
      --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  rm -rf "$INSTALL_ROOT"
  mkdir -p "$INSTALL_ROOT/repo/data"
  cp -a "$repo/scripts" "$INSTALL_ROOT/repo/scripts"
  cp -a "$repo/data/bin" "$INSTALL_ROOT/repo/data/bin"
  chown -R root:root "$INSTALL_ROOT"
  install -m 0644 "$UNIT_SOURCE" "/etc/systemd/system/$UNIT_NAME"
  systemd-analyze verify "/etc/systemd/system/$UNIT_NAME"
  mkdir -p "$STATE_DIR"
  chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
  chmod 0700 "$STATE_DIR"
  rm -rf "$STATE_DIR/evidence" "$STATE_DIR/work"
  mkdir -p "$STATE_DIR/evidence"
  chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR/evidence"
  printf '%s\n' "$sha" > "$STATE_DIR/probe-git-sha"
  chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR/probe-git-sha"
  runuser -u "$SERVICE_USER" -- env HOME="$STATE_DIR" \
    /usr/bin/python3 -c 'import json, socket; print("native probe Python ready")'
  runuser -u "$SERVICE_USER" -- env HOME="$STATE_DIR" \
    "$INSTALL_ROOT/repo/data/bin/velites" --version
  systemctl daemon-reload
}

run_service() {
  require_ci_root
  run_harness runuser e0-runuser-current-no-network,e0-runuser-bwrap-no-net-control
  run_e4_case e4-nnp-yes-cap-empty yes empty
  run_e4_case e4-nnp-no-cap-empty no empty
  run_e4_case e4-nnp-yes-cap-default yes default
  run_e4_case e4-nnp-no-cap-default no default
  systemctl reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
  systemctl start --wait "$UNIT_NAME" &
  local start_wait_pid=$!
  local ready=$STATE_DIR/evidence/n0-ready.json
  for _ in {1..600}; do
    [[ -f $ready ]] && break
    if [[ $(systemctl is-active "$UNIT_NAME" || true) == failed ]]; then
      break
    fi
    sleep 0.1
  done
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
  /usr/bin/python3 - "$ready" "$main_pid" <<'PY'
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
    echo "N0 native probe harness failed; preserving evidence" >&2
    return 1
  }
}

collect_evidence() {
  local output=$1
  require_ci_root
  mkdir -p "$output"
  /usr/bin/python3 "$SCRIPT_DIR/native_sandbox_probe_collect.py" \
    --evidence "$STATE_DIR/evidence" --output "$output" --unit "$UNIT_NAME"
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
