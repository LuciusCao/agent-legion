"""Round-7 attack payloads for the terminal guard (review #707 round 6).

Sister file of test_studio_chat_terminal_guard_round6.py (same helpers,
same subsystem directory): C1 the backtick-body ``\\<newline>`` continuation
inside ``'…'`` regions (a backtick body is re-lexed with backtick rules —
the pair joins the line there, so `echo `make 'prod-\\<NL>down'` really ran
prod-down in all five shells while the guard honored the single-quote
literal rule and allowed) plus M1 the missing ionice WRAPPERS entry
(`ionice kill 1234` reached the shell untouched). Split off at the
800-line threshold (AGENTS.md §4), same convention as round-3/4/5/6.
"""

from __future__ import annotations

import pytest

from server.app.studio_chat.terminal_guard import ensure_terminal_command_allowed

pytestmark = pytest.mark.no_db

NL = "\\\n"


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


class TestBacktickBodyContinuation:
    """backtick 替换体内 ``'…'`` 区的 ``\\<NL>`` 续接（round-7 C1）：
    backtick 体被五 shell 按 backtick 词法重新解析，``\\``+newline 在体
    内任何位置（含 ``'…'`` 区与嵌套 ``$(…)`` 体）都是续接——修前 guard
    对两种替换体共用 in_single 字面规则，六形态全部放行（七轮复审字节
    级对拍：五 shell 的 make 全部收到拼接后的 prod-down 并真实执行）。
    修法：分段器的替换体栈帧携带 body_is_backtick，任何开启的 backtick
    帧重新启用续接分支（嵌套按各自体词法逐层判定）。对照：``$(…)`` 体
    内同写法五 shell 全部字面（round-6 M2，不回归）。"""

    def test_six_forms_all_blocked(self) -> None:
        # 复审触发面六形态（review round-6 C1 表）。
        assert blocked_shell(f"echo `make 'prod-{NL}down'`")
        assert blocked_shell(f"echo \"`make 'prod-{NL}down'`\"")
        assert blocked_shell(f"X=`make 'prod-{NL}down'`")
        assert blocked_shell(f"echo $(x `make 'prod-{NL}down'`)")
        assert blocked_shell(f"echo `x \"$(make 'prod-{NL}down')\"`")
        assert blocked_shell(f"echo `make $'prod-{NL}down'`")

    def test_dollar_bodies_stay_literal_no_regression(self) -> None:
        # $() 体的 round-6 M2 行为不回归：五 shell 全部字面不执行。
        assert not blocked_shell(f"echo \"$(make 'prod-{NL}down')\"")
        assert not blocked_shell(f"echo $(make 'prod-{NL}down')")
        assert not blocked_shell(f'echo "$(x "$(make \'prod-{NL}down\')")"')
        # 顶层 '…'/$'…' 字面（round-5 D2 对照组，五 shell 无拼接）。
        assert not blocked_shell(f"make 'prod-{NL}down'")
        assert not blocked_shell(f"make $'prod-{NL}down'")

    def test_nested_forms_follow_their_own_body_lexer(self) -> None:
        # 嵌套双向：backtick 体内的 $( ) 体仍受外层 backtick span 统治
        # （五 shell 实测同样拼接执行）；$( ) 体内的 backtick 体按 backtick
        # 词法判定（同样拼接）。深嵌套混合形态一并钉住。
        assert blocked_shell(f"echo `x $(make 'prod-{NL}down')`")
        assert blocked_shell(f"echo \"$(x `make 'prod-{NL}down'`)\"")
        assert blocked_shell(f"echo `echo \"$(make 'prod-{NL}down')\"`")
        assert blocked_shell(f"echo \"$(x `y $(make 'prod-{NL}down')`)\"")

    def test_other_backslashes_stay_literal_in_backtick_singles(self) -> None:
        # backtick 体内 '…' 区的其他反斜杠对五 shell 保留字面（只有裸
        # \<NL> 是续接）——guard 的字面读法保持 ALLOW，不扩大拦截面。
        assert not blocked_shell("echo `make 'a\\;b'`")
        assert not blocked_shell("echo `make 'a\\$b'`")
        assert not blocked_shell("echo `make 'a\\\\b'`")
        assert not blocked_shell(f"echo `make 'a\\\\{NL}b'`")


class TestIoniceWrapper:
    """ionice 接入 WRAPPERS（round-7 M1）：`_wrapper_value_opts` 的
    ``("ionice", ("-n",))`` 是 round-1 起的死代码——WRAPPERS 没有
    ionice，`ionice kill 1234` 曾整体放行而真实 ionice（util-linux）把
    kill 作为命令真实执行。表按 util-linux 2.38 实测更正：-c/--class 与
    -n/--classdata 吃值，-t/--ignore 布尔；-p/-P/-u 是“作用于已运行进
    程”模式（多余位置词按 PID/UID 解析报错、不执行命令），不进表。"""

    def test_ionice_wrapped_commands_blocked(self) -> None:
        assert blocked_exec("ionice", ["kill", "1234"])
        assert blocked_exec("ionice", ["-n", "7", "pkill", "-f", "uvicorn"])
        assert blocked_exec("ionice", ["-c2", "-n7", "systemctl", "stop", "docker"])
        assert blocked_shell("ionice -n 7 pkill -f uvicorn")
        assert blocked_shell("ionice --class 2 --classdata 7 make prod-down")

    def test_ionice_benign_forms_stay_allowed(self) -> None:
        assert not blocked_exec("ionice", ["-n", "7", "make", "check-quick"])
        assert not blocked_exec("ionice", ["-c3", "make", "check-quick"])
        # pid/uid 模式的位置词是 PID/UID 参数（真实 ionice 报错不执行）。
        assert not blocked_exec("ionice", ["-p", "123", "kill"])
