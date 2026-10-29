#!/bin/sh
# One-shot Worker named-volume ownership migration. Run before the first up
# (or after stopping an existing worker) with the actual Compose files:
#   sh scripts/migrate-worker-volumes.sh --uid 10001 --gid 10001 -- \
#     -f deploy/compose.worker.yaml -f deploy/compose.worker.pull.example.yaml
set -eu

die() {
  echo "错误: $*" >&2
  exit 1
}

stat_uid() {
  stat -c %u "$1" 2>/dev/null || stat -f %u "$1"
}

stat_gid() {
  stat -c %g "$1" 2>/dev/null || stat -f %g "$1"
}

stat_mode() {
  stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"
}

WORKER_UID=""
WORKER_GID=""
PREFLIGHT_COMPLETE=0
IMAGE_OVERRIDE=""
CURRENT_COMPOSE_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --uid) WORKER_UID="${2:?--uid 缺参数}"; shift 2 ;;
    --gid) WORKER_GID="${2:?--gid 缺参数}"; shift 2 ;;
    --preflight-complete) PREFLIGHT_COMPLETE=1; shift ;;
    --image) IMAGE_OVERRIDE="${2:?--image 缺参数}"; shift 2 ;;
    --current-compose-file) CURRENT_COMPOSE_FILE="${2:?--current-compose-file 缺参数}"; shift 2 ;;
    --) shift; break ;;
    *) die "未知参数: $1（--uid/--gid 后必须以 -- 分隔 Compose 参数）" ;;
  esac
done
[ "$#" -gt 0 ] || die "必须提供实际 Compose 参数（例如 -- -f deploy/compose.worker.yaml）"
case "$WORKER_UID" in
  ''|0|0*|*[!0-9]*) die "UID 必须是无前导零的非 root 数值" ;;
esac
case "$WORKER_GID" in
  ''|0|0*|*[!0-9]*) die "GID 必须是无前导零的非 root 数值" ;;
esac
[ "$WORKER_UID" -le 4294967294 ] || die "UID 超出范围（4294967295 是 chown 不修改哨兵）"
[ "$WORKER_GID" -le 4294967294 ] || die "GID 超出范围（4294967295 是 chown 不修改哨兵）"
command -v docker >/dev/null 2>&1 || die "docker 未安装"

TMPDIR_="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_"' EXIT
trap 'exit 1' INT TERM HUP
if ! docker compose "$@" config --format json >"$TMPDIR_/compose.json" 2>"$TMPDIR_/compose.err"; then
  die "实际 Compose config 失败，未迁移：$(tail -1 "$TMPDIR_/compose.err")"
fi
if [ -z "$IMAGE_OVERRIDE" ]; then
  if ! image="$(docker compose "$@" config --images worker 2>>"$TMPDIR_/compose.err")"; then
    die "实际 Compose Worker 镜像查询失败，未迁移：$(tail -1 "$TMPDIR_/compose.err")"
  fi
else
  image="$IMAGE_OVERRIDE"
fi
case "$image" in
  ''|*"
"*) die "实际 Compose 未解析到唯一非空 Worker 镜像" ;;
esac
if ! resolved="$(docker run --rm -i --network none --entrypoint python3 "$image" -c \
  'import json,sys
config = json.load(sys.stdin)
expected = {"worker-control": "/var/lib/agent-legion-worker-control", "worker-data": "/var/lib/agent-legion-worker"}
mounts = {(item.get("source"), item.get("target")) for item in config["services"]["worker"]["volumes"] if item.get("type") == "volume"}
for source, target in expected.items():
    if (source, target) not in mounts:
        raise ValueError(f"Compose 缺少 named-volume 挂载 {source}:{target}")
print(*(config["volumes"][source]["name"] for source in expected))' \
  <"$TMPDIR_/compose.json" 2>>"$TMPDIR_/compose.err")"; then
  die "无法解析两个 named volumes 及目标挂载点，未迁移：$(tail -1 "$TMPDIR_/compose.err")"
fi
control_volume="${resolved%% *}"
data_volume="${resolved##* }"
[ -n "$control_volume" ] && [ -n "$data_volume" ] && [ "$control_volume" != "$data_volume" ] || die "Compose 卷名解析异常"
if [ -n "$CURRENT_COMPOSE_FILE" ]; then
  if ! docker compose -f "$CURRENT_COMPOSE_FILE" config --format json >"$TMPDIR_/current.json" 2>"$TMPDIR_/current.err"; then
    die "现有 Compose config 失败，未迁移：$(tail -1 "$TMPDIR_/current.err")"
  fi
  if ! current_resolved="$(docker run --rm -i --network none --entrypoint python3 "$image" -c \
    'import json,sys
config = json.load(sys.stdin)
expected = {"worker-control": "/var/lib/agent-legion-worker-control", "worker-data": "/var/lib/agent-legion-worker"}
mounts = {(item.get("source"), item.get("target")) for item in config["services"]["worker"]["volumes"] if item.get("type") == "volume"}
for source, target in expected.items():
    if (source, target) not in mounts:
        raise ValueError(f"Compose 缺少 named-volume 挂载 {source}:{target}")
print(*(config["volumes"][source]["name"] for source in expected))' \
    <"$TMPDIR_/current.json" 2>>"$TMPDIR_/current.err")"; then
    die "无法解析现有 named volumes，未迁移：$(tail -1 "$TMPDIR_/current.err")"
  fi
  [ "$current_resolved" = "$resolved" ] || die "现有/目标 Compose 卷身份不一致（${current_resolved} vs ${resolved}），拒绝误 chown"
fi
if [ "$PREFLIGHT_COMPLETE" = 0 ]; then
bootstrap_source="$(docker run --rm -i --network none --entrypoint python3 "$image" -c \
  'import json,sys
config = json.load(sys.stdin)
for item in config["services"]["worker"]["volumes"]:
    if item.get("type") == "bind" and item.get("target") == "/etc/agent-legion/worker.yaml":
        print(item["source"])
        break' <"$TMPDIR_/compose.json" 2>>"$TMPDIR_/compose.err")" || die "无法定位 bootstrap worker.yaml 宿主路径"
case "$bootstrap_source" in
  */worker.remote.example.yaml|*/worker.host.example.yaml)
    # 仓库自带的非 secret 示例允许 0644；自定义 secret 按 installer 同一安全语义。
    ;;
  *)
    [ -f "$bootstrap_source" ] && [ ! -L "$bootstrap_source" ] || die "bootstrap 必须是非 symlink regular file: $bootstrap_source"
    [ "$(stat_mode "$bootstrap_source")" = 600 ] || die "bootstrap $bootstrap_source 必须精确 0600，拒绝 stop/chown"
    [ "$(stat_uid "$bootstrap_source")" = "$WORKER_UID" ] && [ "$(stat_gid "$bootstrap_source")" = "$WORKER_GID" ] || die "bootstrap $bootstrap_source 的共同数值 UID/GID 必须等于运行身份 $WORKER_UID:$WORKER_GID"
    ;;
esac
# This root one-shot only reads the exact mounts produced by Compose. It never
# starts the service or makes the worker root long-lived.
if ! docker compose "$@" run --rm --no-deps --user 0:0 --entrypoint python3 worker -c \
  'from pathlib import Path
import os,stat,sys,yaml
from worker.config_validation import validate_config
bootstrap = Path("/etc/agent-legion/worker.yaml")
state = Path("/var/lib/agent-legion-worker-control/worker.yaml")
models = Path("/home/agent-worker/.velites/models.json")
binary = Path("/app/data/bin/velites")
def regular(path, required=True):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{path} 必须是非 symlink regular file")
    if required and not path.is_file():
        raise ValueError(f"缺少 regular file: {path}")
regular(bootstrap)
regular(state, required=False)
if state.exists() and stat.S_IMODE(state.lstat().st_mode) != 0o600:
    raise ValueError("控制卷 worker.yaml 必须精确 0600；不会放宽 mode 或执行 stop/chown")
regular(models, required=False)
if models.exists() and stat.S_IMODE(models.lstat().st_mode) != 0o600:
    raise ValueError("models.json 必须精确 0600，且能由目标 UID 实际读取")
if binary.exists() or binary.is_symlink():
    regular(binary)
    mode = stat.S_IMODE(binary.lstat().st_mode)
    if mode & 0o444 != 0o444 or mode & 0o111 != 0o111:
        raise ValueError("velites 必须 a+rx")
source = state if state.is_file() else bootstrap
with source.open(encoding="utf-8") as stream:
    config = validate_config(yaml.safe_load(stream), require_identity=False)
roots = (Path("/var/lib/agent-legion-worker"), Path("/var/lib/agent-legion-worker-control"))
def check_path(key, value):
    path = Path(str(value))
    root = next((root for root in roots if path == root or root in path.parents), None)
    if not path.is_absolute() or ".." in path.parts or root is None:
        raise ValueError(f"{key}={value} 不在 named-volume 迁移覆盖树下")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{key}={value} 经过卷内 symlink {current}")
check_path("work_root", config.get("work_root", "/var/lib/agent-legion-worker"))
for key in ("register_token_file", "register_token_dir"):
    value = config.get(key)
    if value:
        check_path(key, value)' >"$TMPDIR_/preflight.log" 2>&1; then
  die "控制副本/custom 路径/secret/binary 预检失败，未 stop 或迁移：$(tail -5 "$TMPDIR_/preflight.log")"
fi
# Prove bind-parent traversal and secret readability as the target identity,
# without changing their mode or ownership.
if ! docker compose "$@" run --rm --no-deps --user "$WORKER_UID:$WORKER_GID" --entrypoint python3 worker -c \
  'import os
from pathlib import Path
for value in ("/etc/agent-legion/worker.yaml", "/home/agent-worker/.velites/models.json"):
    path = Path(value)
    if path.is_file():
        with path.open("rb") as stream:
            stream.read(1)
binary = Path("/app/data/bin/velites")
if binary.exists() and not os.access(binary, os.R_OK | os.X_OK):
    raise PermissionError("velites 不可由运行 UID 执行")' >"$TMPDIR_/identity-probe.log" 2>&1; then
  die "目标 UID 无法遍历父目录或读取 0600 secret，未 stop 或迁移：$(tail -1 "$TMPDIR_/identity-probe.log")"
fi
fi
if [ -n "$CURRENT_COMPOSE_FILE" ]; then
  if ! docker compose -f "$CURRENT_COMPOSE_FILE" stop worker >"$TMPDIR_/stop.log" 2>&1; then
    die "按现有 Compose 停止 Worker 失败，未迁移：$(tail -1 "$TMPDIR_/stop.log")"
  fi
  running="$(docker compose -f "$CURRENT_COMPOSE_FILE" ps --status running -q worker 2>"$TMPDIR_/ps.err")" || die "无法按现有 Compose 重验 Worker 停止状态：$(tail -1 "$TMPDIR_/ps.err")"
else
  if ! docker compose "$@" stop worker >"$TMPDIR_/stop.log" 2>&1; then
    die "停止 Worker 失败，未迁移：$(tail -1 "$TMPDIR_/stop.log")"
  fi
  running="$(docker compose "$@" ps --status running -q worker 2>"$TMPDIR_/ps.err")" || die "无法重验 Worker 停止状态：$(tail -1 "$TMPDIR_/ps.err")"
fi
[ -z "$running" ] || die "Worker 仍在运行（${running}），拒绝并发 chown"
if ! docker run --rm --network none --user 0:0 --entrypoint python3 \
  -v "$control_volume:/var/lib/agent-legion-worker-control" \
  -v "$data_volume:/var/lib/agent-legion-worker" \
  "$image" -c \
  'import os,stat,sys
uid,gid = (int(value) for value in sys.argv[1:])
def migrate(path):
    mode = os.lstat(path).st_mode
    os.chown(path, uid, gid, follow_symlinks=False)
    if stat.S_ISDIR(mode):
        with os.scandir(path) as entries:
            for entry in entries:
                migrate(entry.path)
for root in ("/var/lib/agent-legion-worker-control", "/var/lib/agent-legion-worker"):
    migrate(root)
    for directory, dirs, files in os.walk(root):
        for name in [os.path.basename(directory), *dirs, *files]:
            path = directory if name == os.path.basename(directory) else os.path.join(directory, name)
            owner = os.lstat(path)
            assert (owner.st_uid, owner.st_gid) == (uid, gid), (path, owner.st_uid, owner.st_gid)' "$WORKER_UID" "$WORKER_GID" >"$TMPDIR_/chown.log" 2>&1; then
  die "named-volume chown 失败：$(tail -1 "$TMPDIR_/chown.log")"
fi
echo "==> 两个 named volumes 已迁移到 $WORKER_UID:${WORKER_GID}；可执行 docker compose up -d"
