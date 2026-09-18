"""Round-3 attack payloads for the terminal guard (review #707 round 2).

Sister file of test_studio_chat_terminal_guard.py (same helpers, same
subsystem directory): H1 backtick/`$()` escape ambiguity (ksh divergence —
malformed-即拦), H2 host-root mount path-normalization spellings, M1
declare/typeset/builtin export injection wrappers, M2 ZDOTDIR. The round-1/2
payload classes stay in the parent file; this one holds the round-3 increment
only, split off at the 800-line threshold (AGENTS.md §4).
"""

from __future__ import annotations

import pytest

from server.app.studio_chat.terminal_guard import ensure_terminal_command_allowed

pytestmark = pytest.mark.no_db


def blocked_exec(
    command: str, args: list[str] | None = None, env: list[tuple[str, str]] | None = None
) -> bool:
    try:
        ensure_terminal_command_allowed(command, args, env)
    except Exception as exc:  # noqa: BLE001 - test helper, re-raised below
        assert "prod-restart.sh" in str(exc), "block message must point at the atomic entry"
        assert "issue #629" in str(exc), "block message must cite the incident"
        return True
    return False


def blocked_shell(text: str) -> bool:
    return blocked_exec("sh", ["-c", text])


class TestSubstitutionEscapeAmbiguity:
    """替换体转义歧义形态全拦（round-3 H1）：ksh 93u+ 对替换体内 `\\`` 不认
    转义（与 POSIX/bash/dash/zsh 相反），``echo \\`x\\` ; cmd`` 在 ksh 真实执
    行第二段（launchctl/make shim 实测落地）、其余四 shell parse error——
    guard 无法同时拟合两种语义，未闭合的引号/替换体一律 fail-safe 拦截。"""

    def test_backtick_escape_ambiguity_forms(self) -> None:
        # 复审 H1 原始形态（宿主双引号内 `\``，替换体开放到 EOF）。
        assert blocked_shell('echo "`x\\`" ; make prod-down')
        assert blocked_shell('echo "`x\\`" ; kill 1234')
        assert blocked_shell("ksh -c 'echo \"`x\\`\" ; make prod-down'")
        # ksh 实测活体形态：未加宿主引号的替换体 + `;` 第二段。
        assert blocked_shell("echo `x\\` ; make prod-down")
        assert blocked_shell("echo `x\\` ; launchctl bootout gui/501/com.x")

    def test_dollar_paren_escape_flip_retracted(self) -> None:
        """round-2 的 `$(x\\)` 放行翻转收回：该形态与 backtick 族同属替换体
        内转义歧义，两 shell 家族语义相反，无法两全时按 fail-safe 拦。"""
        assert blocked_shell('echo "$(x\\)" ; make prod-down')
        assert blocked_shell('echo "$(x\\)" ; kill 1')

    def test_unclosed_plain_quotes_are_refused(self) -> None:
        """未闭合的裸引号同属 malformed 家族：单引号、双引号、裸 `$(` 与裸
        backtick 开放到行尾都拦（五 shell 全部 parse error 的形态，拦了只会
        过严不会漏）。"""
        assert blocked_shell('echo "unclosed ; make prod-down')
        assert blocked_shell("echo 'unclosed ; kill 1")
        assert blocked_shell("echo $(x ; make prod-down")
        assert blocked_shell("echo `x ; make prod-down")

    def test_closed_substitutions_are_not_collateral(self) -> None:
        """正确放行对照：合法闭合替换体不落入未闭合拦截——内部命令照常解析
        （H1 修复不得误伤正常形态）。"""
        assert blocked_shell('echo "`x`" ; make prod-down')  # 闭合后第二段照拦
        assert blocked_shell('echo "$(x)" ; make prod-down')
        assert blocked_shell('echo "$(make prod-down)"')  # 替换体内命令照拦
        # 完整闭合、无 denylist 内容的正常命令放行。
        assert not blocked_shell('echo "`date`" ; ls -la')
        assert not blocked_shell('echo "$(echo hi)" ; make check-quick')
        # 已闭合替换体的转义形态（`\\)` 转义后真闭括号）保持 round-2 行为。
        assert blocked_shell('echo "$(x\\) ; make prod-down)"')
        assert not blocked_shell('echo "$(x\\))"')


class TestDockerHostRootSpellings:
    """docker run 宿主根挂载的路径规范化拼写（round-3 H2）：docker/内核 bind
    mount 先归一化 source 再挂载，`//`、`/./`、`/../`、`/proc/self/root`（及
    `source=` 拼写、`--volume=`、`--mount=` 内联）全部挂出宿主根——docker
    29.7.2 实测（macOS Desktop 下经 /host/host_mnt 读到宿主 marker 文件）。
    比对前 normpath + 前导斜杠折叠 + /proc/{self,1}/root 显式拒。"""

    def test_normalized_root_spellings_mount(self) -> None:
        for src in ("//", "/./", "/../", "/proc/self/root", "/proc/1/root"):
            assert blocked_shell(f"docker run --mount type=bind,src={src},dst=/host alpine sh")
            assert blocked_shell(f"docker run -v {src}:/host alpine sh")
        # source= 拼写与 --mount=/--volume= 内联形态。
        assert blocked_shell(
            "docker run --mount type=bind,source=/proc/self/root,dst=/host alpine sh"
        )
        assert blocked_shell("docker run --volume=//:/host alpine sh")
        assert blocked_shell("docker run --mount=type=bind,src=//,dst=/host alpine sh")

    def test_non_root_mounts_stay_allowed(self) -> None:
        """正确放行对照：非根挂载（绝对数据目录、`//data` 前导双斜杠的数据
        路径、volume/tmpfs/dst-only）不因规范化误伤；`/proc/self/root/..` 规
        范化为 /proc/self（容器自身 procfs，实测挂出 attr/autogroup），非宿主
        根，保持放行。"""
        assert not blocked_shell("docker run --mount type=bind,src=/data,dst=/data alpine ls")
        assert not blocked_shell("docker run -v //data:/data alpine ls")
        assert not blocked_shell("docker run -v /data:/data alpine ls")
        assert not blocked_shell("docker run --mount type=volume,src=x,dst=/x alpine sh")
        assert not blocked_shell("docker run --mount dst=/host alpine sh")
        assert not blocked_shell("docker run --mount tmpfs,dst=/tmp alpine sh")
        assert not blocked_shell(
            "docker run --mount type=bind,src=/proc/self/root/..,dst=/host alpine sh"
        )

    def test_separated_v_flag_with_data_spec(self) -> None:
        """分离形态 `-v <spec>` 的数据挂载不误伤：flag token 本身不得被当成
        空 source 的挂载规范（`-v` + `//data:/data` 双计数曾让空 source 归一
        化成 `/` 而误拦）。"""
        assert not blocked_shell("docker run -v //data:/data alpine ls")
        assert not blocked_exec("docker", ["run", "-v", "/data:/data", "alpine", "ls"])
        assert blocked_exec("docker", ["run", "-v", "//:/host", "alpine", "sh"])


class TestDeclareExportWrappers:
    """declare/typeset -x 与 builtin export 的注入（round-3 M1）：`declare -x
    BASH_ENV=…; bash -c …` 是 export 注入的另一拼写（bash 实测 marker 先落
    地），经同一 env 赋值门检查；无 -x 的 declare 只设 shell 变量、+x 移除导
    出属性（实测均不注入），不得误伤。"""

    def test_declare_typeset_export_forms(self) -> None:
        assert blocked_shell("declare -x BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_shell("typeset -x BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_shell("declare -gx BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_shell("declare -x PATH=.:/bin; make check")
        assert blocked_shell("typeset -x ZDOTDIR=/tmp/evil; zsh -c 'echo hi'")
        # exec 形态（ACP argv 预切分）。
        assert blocked_exec("declare", ["-x", "BASH_ENV=/tmp/x.sh"])

    def test_builtin_export_form(self) -> None:
        assert blocked_shell("builtin export BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_shell("builtin declare -x BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_exec("builtin", ["export", "BASH_ENV=/tmp/x.sh"])
        # builtin 的其余用法保持登记缺口（builtin kill 不因此进入 denylist）。
        assert not blocked_shell("builtin kill 1234")

    def test_non_exporting_declare_stays_allowed(self) -> None:
        """正确放行对照：无 -x 的 declare/typeset 只设 shell 变量（实测后续
        bash 不 source），-p 是查询、+x 是移除导出属性；合法导出普通变量照常
        放行。"""
        assert not blocked_shell("declare -i x=1; echo $x")
        assert not blocked_shell("declare BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert not blocked_shell("declare +x BASH_ENV=1; bash -c 'echo hi'")
        assert not blocked_shell("declare -p BASH_ENV")
        assert not blocked_shell("typeset -i count=3; echo $count")
        assert not blocked_shell("declare -x FOO=bar; make check-quick")
        assert not blocked_shell("builtin export LANG=C.UTF-8; make check-quick")


class TestZdotdirInjection:
    """ZDOTDIR 注入（round-3 M2）：zsh 从 $ZDOTDIR source .zshenv（实测
    marker 落地），是 BASH_ENV 的 zsh 等价物——进 BLOCKED_ENV_NAMES，与既有
    三通道（env 参数 / 赋值前缀 / export 段）同表拦截。"""

    def test_zdotdir_all_channels(self) -> None:
        assert blocked_shell("ZDOTDIR=/tmp/evil zsh -c 'echo hi'")
        assert blocked_shell("env ZDOTDIR=/tmp/evil zsh -c 'echo hi'")
        assert blocked_shell("export ZDOTDIR=/tmp/evil; zsh -c 'echo hi'")
        assert blocked_exec("zsh", ["-c", "echo hi"], env=[("ZDOTDIR", "/tmp/evil")])

    def test_benign_zsh_usage_stays_allowed(self) -> None:
        """正确放行对照：普通 env 前缀与 zsh 直调不受影响。"""
        assert not blocked_shell("FOO=bar zsh -c 'echo hi'")
        assert not blocked_shell("zsh -c 'echo hi'")
        assert not blocked_exec("zsh", ["-c", "echo hi"])
