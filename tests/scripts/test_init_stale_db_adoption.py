"""init-worktree.sh 静默收养护栏（#1349，#1246 复验 P2-2）。

clean-worktree.sh 在「git worktree remove」与「drop 派生库」之间被 SIGKILL
时，旧实例的派生库留存；此后同名 worktree 重建并跑 init-worktree.sh，
createdb 命中「已存在可忽略」会**静默复用旧库**——跨实例数据沿用，即
#1084 目标缺陷形态的残余窗口。init 侧收口：取派生名锁后、改写 .env 之前
发现派生库已存在，而本 worktree 既无初始化完成标记、.env 也未指向自身
派生库时 fail-fast，并给出收口路径（drop-worktree-db.sh 清残留后重跑，
或显式 AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1 复核沿用）。

测试用真实 git 仓库与真实 `git worktree add/remove`（worktree 注册语义
要真的），psql/createdb/dropdb/uv 走受限 PATH 上的桩：库存在性由
$STUB_PG_DIR/<库名> 目录模拟，不碰真实数据库。
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = (
    "init-worktree.sh",
    "drop-worktree-db.sh",
    "worktree-names-lib.sh",
    "ensure-s3-bucket.py",
    "uv_cache_prewarm.py",
)
_GIT = shutil.which("git")
_DEV_DB = "agent_legion_victim"
_TEST_DB = "agent_legion_test_victim"

# psql 桩：库存在性由 $STUB_PG_DIR/<库名> 目录模拟；role 探测永远查不到
# agent_legion_dev（跳过属主对齐）；STUB_PSQL_DOWN 指向的文件存在时，库级
# 探测连接级失败（模拟 Postgres 不可达）。
_PSQL_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
query="${*: -1}"
if [[ "$query" == *"select 1 from pg_roles"* ]]; then exit 0; fi
if [[ -n "${STUB_PSQL_DOWN:-}" && -e "$STUB_PSQL_DOWN" && "$query" == *pg_database* ]]; then
    echo "psql: error: connection to server failed" >&2
    exit 1
fi
for dir in "$STUB_PG_DIR"/*; do
    [[ -d "$dir" ]] || continue
    db="$(basename "$dir")"
    if [[ "$query" == *"datname='$db'"* ]]; then
        if [[ "$query" == *pg_size_pretty* ]]; then echo "stub_owner|1 MB"; else echo 1; fi
    fi
done
exit 0
"""

# createdb 桩：已存在则报错退出 1（真实 createdb 的语义），否则「建库」；
# STUB_PSQL_DOWN 指向的文件存在时连接级失败（Postgres 不可达）。
_CREATEDB_STUB = """#!/usr/bin/env bash
printf 'createdb %s\\n' "$*" >>"$STUB_LOG"
if [[ -n "${STUB_PSQL_DOWN:-}" && -e "$STUB_PSQL_DOWN" ]]; then
    echo "createdb: error: connection to server failed" >&2
    exit 1
fi
if [[ -d "$STUB_PG_DIR/$1" ]]; then
    echo "createdb: error: database \\"$1\\" already exists" >&2
    exit 1
fi
mkdir -p "$STUB_PG_DIR/$1"
"""

# dropdb 桩：删掉存在性目录（真实 dropdb 的语义）。
_DROPDB_STUB = """#!/usr/bin/env bash
printf 'dropdb %s\\n' "$*" >>"$STUB_LOG"
rm -rf "$STUB_PG_DIR/$1"
"""

# uv 桩：vault key 生成拿到一行输出；S3 建桶步骤静默成功。
_UV_STUB = """#!/usr/bin/env bash
echo "stub-vault-master-key"
"""


def _exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        assert _GIT is not None
        self.tmp = tmp_path.resolve()
        (self.tmp / "home").mkdir()
        self.main = self.tmp / "main"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.pg = self.tmp / "pg"
        self.pg.mkdir()
        self.log = self.tmp / "stub.log"
        self.log.write_text("", encoding="utf-8")
        _exe(self.bin / "git", f'#!/usr/bin/env bash\nexec "{_GIT}" "$@"\n')
        _exe(self.bin / "psql", _PSQL_STUB)
        _exe(self.bin / "createdb", _CREATEDB_STUB)
        _exe(self.bin / "dropdb", _DROPDB_STUB)
        _exe(self.bin / "uv", _UV_STUB)
        self.git("init", "-q", str(self.main))
        self.git("-C", str(self.main), "commit", "-q", "--allow-empty", "-m", "init")
        self._n = 0

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_LOG": str(self.log),
            "STUB_PG_DIR": str(self.pg),
            **extra,
        }

    def git(self, *args: str) -> None:
        assert _GIT is not None
        subprocess.run([_GIT, *args], check=True, capture_output=True, env=self.env())

    def add(self, name: str) -> Path:
        self._n += 1
        path = self.main / ".worktrees" / name
        self.git("-C", str(self.main), "worktree", "add", "-q", "-b", f"b{self._n}", str(path))
        return path

    def install_scripts(self, worktree: Path) -> None:
        scripts = worktree / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in _SCRIPTS:
            shutil.copy(ROOT / "scripts" / name, scripts / name)

    def run(self, script: Path, *args: str, extra_env: dict[str, str] | None = None):
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=self.env(**(extra_env or {})),
            cwd=self.main,
            timeout=60,
        )

    def lock_holders(self) -> list[str]:
        lock_dir = self.main / ".git" / "worktree-derived-names.lock"
        if not lock_dir.exists():
            return []
        return sorted(p.name for p in lock_dir.iterdir())


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    if _GIT is None:
        pytest.skip("git not available")
    return _Env(tmp_path)


def _seed_stale_remnant(env: _Env, stale: tuple[str, ...] = ("dev", "test")) -> None:
    """布置 #1349 事故现场：旧实例 init 成功（建库+标记），随后 clean 在
    git worktree remove 之后、drop 派生库之前被 SIGKILL——worktree 已移除
    （真实 git worktree remove），stale 指定的派生库留存。"""
    victim = env.add("victim")
    env.install_scripts(victim)
    (victim / ".env").write_text("# stub env\n", encoding="utf-8")
    result = env.run(victim / "scripts" / "init-worktree.sh")
    assert result.returncode == 0, result.stderr
    assert (env.pg / _DEV_DB).is_dir()
    # 用过的旧实例跑过测试：测试库也已建立（由测试基建，不经 init）。
    (env.pg / _TEST_DB).mkdir()
    env.git("-C", str(env.main), "worktree", "remove", "--force", str(victim))
    if "dev" not in stale:
        shutil.rmtree(env.pg / _DEV_DB)
    if "test" not in stale:
        shutil.rmtree(env.pg / _TEST_DB)


def _rebuild_same_name(env: _Env) -> Path:
    """同名同路径重建 worktree（裸 git add，新分支），.env 从基准复制而来
    （尚未指向本实例派生库）——即 #1349 里「拿锁后 createdb 命中已存在」
    的新实例初始状态。"""
    victim = env.add("victim")
    env.install_scripts(victim)
    (victim / ".env").write_text("# stub env\n", encoding="utf-8")
    return victim


@pytest.mark.parametrize("stale", [("dev", "test"), ("dev",), ("test",)])
def test_init_refuses_stale_db_left_behind_by_interrupted_clean(
    env: _Env, stale: tuple[str, ...]
) -> None:
    """核心回归（#1349）：clean SIGKILL 残留的派生库（开发库/测试库任一）
    不得被同名新实例静默收养——init 必须 fail-fast 且不产生任何副作用。"""
    _seed_stale_remnant(env, stale)
    victim = _rebuild_same_name(env)
    env.log.write_text("", encoding="utf-8")

    result = env.run(victim / "scripts" / "init-worktree.sh")

    assert result.returncode == 1
    assert "#1349" in result.stderr
    assert "静默复用" in result.stderr
    stale_dbs = {"dev": _DEV_DB, "test": _TEST_DB}
    for kind in stale:
        assert stale_dbs[kind] in result.stderr
    # fail-fast 在改写 .env、建库、写完成标记之前——新实例保持「待初始化」，
    # 删除护栏不会把它当资源主，drop-worktree-db.sh 可以收口残留。
    assert (victim / ".env").read_text(encoding="utf-8") == "# stub env\n"
    assert not (victim / ".worktree-initialized.ok").exists()
    assert "createdb" not in env.log.read_text(encoding="utf-8")
    # 残留库不被误删（处置由人复核后决定）。
    for kind in stale:
        assert (env.pg / stale_dbs[kind]).is_dir()
    # 锁已由 EXIT trap 释放，不留 holder。
    assert env.lock_holders() == []


def test_stale_remnant_can_be_dropped_then_reinitialized(env: _Env) -> None:
    """收口路径端到端：init 拒绝后，drop-worktree-db.sh 删掉残留（被拒绝的
    新实例无标记、.env 未指向派生库，是待初始化登记、不拦删除），重跑 init
    在干净石板上正常建库。"""
    _seed_stale_remnant(env)
    victim = _rebuild_same_name(env)
    refused = env.run(victim / "scripts" / "init-worktree.sh")
    assert refused.returncode == 1

    runner = env.add("runner")
    env.install_scripts(runner)
    drop = env.run(runner / "scripts" / "drop-worktree-db.sh", "victim", "--yes")
    assert drop.returncode == 0, drop.stderr
    assert f"已删除: {_DEV_DB}" in drop.stdout
    assert f"已删除: {_TEST_DB}" in drop.stdout
    assert not (env.pg / _DEV_DB).exists()
    assert not (env.pg / _TEST_DB).exists()

    reinit = env.run(victim / "scripts" / "init-worktree.sh")
    assert reinit.returncode == 0, reinit.stderr
    assert f"已创建数据库 {_DEV_DB}" in reinit.stdout
    assert (victim / ".worktree-initialized.ok").exists()
    assert f"postgresql://127.0.0.1:5432/{_DEV_DB}" in (victim / ".env").read_text(encoding="utf-8")
    assert (env.pg / _DEV_DB).is_dir()
    assert env.lock_holders() == []


def test_init_adopt_existing_db_opt_in(env: _Env) -> None:
    """显式复核沿用：AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1 时带着显眼警告
    收养已存在的派生库（ deliberate 继承旧实例数据的合法出口）。"""
    _seed_stale_remnant(env)
    victim = _rebuild_same_name(env)

    result = env.run(
        victim / "scripts" / "init-worktree.sh",
        extra_env={"AGENT_LEGION_INIT_ADOPT_EXISTING_DB": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert "AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1 沿用已存在的派生库" in result.stderr
    assert _DEV_DB in result.stderr
    assert (victim / ".worktree-initialized.ok").exists()
    # 收养的旧库原样保留（未重建、未删除）。
    assert (env.pg / _DEV_DB).is_dir()


def test_legacy_instance_without_marker_reruns(env: _Env) -> None:
    """存量实例幂等重跑不误伤：无完成标记但 .env 已指向自身派生库（标记
    机制引入前创建的实例），已存在的库就是自己的——提示后沿用。"""
    _seed_stale_remnant(env)
    victim = _rebuild_same_name(env)
    (victim / ".env").write_text(
        f"AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/{_DEV_DB}\n",
        encoding="utf-8",
    )

    result = env.run(victim / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    assert "存量实例幂等重跑" in result.stdout
    assert (victim / ".worktree-initialized.ok").exists()


def test_rerun_with_marker_skips_stale_check(env: _Env) -> None:
    """幂等重跑不误伤：有完成标记时不做检查——即使 Postgres 完全不可达
    （库级探测全部失败）也照常走完初始化。"""
    victim = env.add("victim")
    env.install_scripts(victim)
    (victim / ".env").write_text("# stub env\n", encoding="utf-8")
    first = env.run(victim / "scripts" / "init-worktree.sh")
    assert first.returncode == 0, first.stderr
    assert (victim / ".worktree-initialized.ok").exists()

    down = env.tmp / "psql-down"
    down.write_text("down\n", encoding="utf-8")
    env.log.write_text("", encoding="utf-8")
    result = env.run(
        victim / "scripts" / "init-worktree.sh", extra_env={"STUB_PSQL_DOWN": str(down)}
    )

    assert result.returncode == 0, result.stderr
    # 有标记时完全不探测库存在性。
    assert "select 1 from pg_database" not in env.log.read_text(encoding="utf-8")


def test_probe_failure_degrades_with_warning(env: _Env) -> None:
    """声明的降级路径：psql 在但 Postgres 不可达时探测失败 ≠ 库不存在，
    保持离线初始化的宽容语义——提示跳过检查、照常完成（此刻 createdb
    同样不可用，不产生任何库动作）。"""
    victim = env.add("victim")
    env.install_scripts(victim)
    (victim / ".env").write_text("# stub env\n", encoding="utf-8")
    down = env.tmp / "psql-down"
    down.write_text("down\n", encoding="utf-8")

    result = env.run(
        victim / "scripts" / "init-worktree.sh", extra_env={"STUB_PSQL_DOWN": str(down)}
    )

    assert result.returncode == 0, result.stderr
    assert "跳过静默收养检查" in result.stderr
    assert (victim / ".worktree-initialized.ok").exists()


def test_superstring_base_env_is_not_misjudged_as_own(env: _Env) -> None:
    """复验 P1-1 回归：BASE 派生库名是自身超串（victim2 ⊃ victim）时，本趟
    从 BASE 复制的 .env 不得被宽松子串匹配误判为「指向自身派生库」——子串
    匹配是删除护栏的 fail-closed 设计，在 init 语境方向反转成 fail-open，
    会把 #1349 的目标缺陷原样放行。init 必须拒绝（归属判定只认严格锚定
    形态），并移除本趟复制的 .env；随后 drop-worktree-db.sh 收口路径可用
    （无标记 + 无 .env = 待初始化登记，不被宽松兜底误判为资源主）。"""
    # BASE：健康存活的 victim2，.env 指向自己的派生库（严格形态，与 init
    # 自身产出一致）——其库名 agent_legion_victim2 是 victim 派生库名的超串。
    base = env.add("victim2")
    (base / ".env").write_text(
        "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_victim2\n"
        "AGENT_LEGION_S3_BUCKET=agent-legion-victim2\n",
        encoding="utf-8",
    )
    _seed_stale_remnant(env)
    victim = env.add("victim")  # 同名重建，不带 .env——init 本趟从 BASE 复制
    env.install_scripts(victim)
    env.log.write_text("", encoding="utf-8")

    result = env.run(victim / "scripts" / "init-worktree.sh", str(base))

    assert result.returncode == 1
    assert "#1349" in result.stderr
    # 不得走「存量实例幂等重跑」臂（宽松子串兜底的误判臂）。
    assert "存量实例" not in result.stdout
    # P2-1：本趟从 BASE 复制的 .env 已移除（它指向 BASE 的库/bucket）。
    assert "本趟从基准复制的 .env 已移除" in result.stderr
    assert not (victim / ".env").exists()
    assert not (victim / ".worktree-initialized.ok").exists()
    assert "createdb" not in env.log.read_text(encoding="utf-8")
    # 残留库不被误删。
    assert (env.pg / _DEV_DB).is_dir()
    assert (env.pg / _TEST_DB).is_dir()

    # 二阶后果解除：被拒实例无标记且无 .env，删除护栏视其为待初始化登记，
    # drop-worktree-db.sh 收口残留畅通（P1-1 修复前宽松兜底会按
    # 「.env 含 agent_legion_victim 子串」误判资源主而拒删）。
    runner = env.add("runner")
    env.install_scripts(runner)
    drop = env.run(runner / "scripts" / "drop-worktree-db.sh", "victim", "--yes")
    assert drop.returncode == 0, drop.stderr
    assert f"已删除: {_DEV_DB}" in drop.stdout
    assert f"已删除: {_TEST_DB}" in drop.stdout

    # 清干净后重跑 init：重新从 BASE 复制 .env 并改写为本实例的派生值。
    reinit = env.run(victim / "scripts" / "init-worktree.sh", str(base))
    assert reinit.returncode == 0, reinit.stderr
    env_text = (victim / ".env").read_text(encoding="utf-8")
    assert f"postgresql://127.0.0.1:5432/{_DEV_DB}" in env_text
    assert "AGENT_LEGION_S3_BUCKET=agent-legion-victim\n" in env_text
    assert "victim2" not in env_text
    assert (victim / ".worktree-initialized.ok").exists()
    assert env.lock_holders() == []


def test_preexisting_env_is_kept_on_refusal(env: _Env) -> None:
    """P2-1 另一臂：pre-existing .env（非本趟复制）在拒绝时原样保留——即使
    其内容是手改形态（引号包裹的自身派生库 URL，严格锚定不认、init 语境
    判「未指向」而拒绝），也不动用户自己放的文件。"""
    _seed_stale_remnant(env)
    victim = _rebuild_same_name(env)
    quoted = f'AGENT_LEGION_DATABASE_URL="postgresql://127.0.0.1:5432/{_DEV_DB}"\n'
    (victim / ".env").write_text(quoted, encoding="utf-8")

    result = env.run(victim / "scripts" / "init-worktree.sh")

    assert result.returncode == 1
    assert "#1349" in result.stderr
    # 手改形态严格锚定不认 → 不走存量臂；但 pre-existing .env 不动。
    assert "存量实例" not in result.stdout
    assert (victim / ".env").read_text(encoding="utf-8") == quoted
    assert (env.pg / _DEV_DB).is_dir()
