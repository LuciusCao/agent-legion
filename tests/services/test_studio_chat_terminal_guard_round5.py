"""Round-5 attack payloads for the terminal guard (review #707 round 4).

Sister file of test_studio_chat_terminal_guard.py (same helpers, same
subsystem directory): C1/C2 backslash-newline continuations inside double
quotes, ``$"…"`` locale quotes and $()/backtick bodies (the round-4 fix
only dropped them in the UNQUOTED context — one pair of quotes un-masked
the denylist word), C3 xargs --process-slot-var as a required-argument
option (GNU 4.9.0: the bare form eats the next word as the variable name).
Split off at the 800-line threshold (AGENTS.md §4), same convention as the
round-3/round-4 sister files.
"""

from __future__ import annotations

import pytest

from server.app.studio_chat.terminal_guard import ensure_terminal_command_allowed

pytestmark = pytest.mark.no_db

# A real backslash + newline: written as a variable so every payload spells
# the continuation exactly once (it must be the two literal characters a
# shell sees, not this file's line layout).
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


class TestQuotedContinuationsJoin:
    """双引号/locale 引号内的 ``\\<newline>`` 续接（round-5 C1/C2）：五 shell
    实测 `make "prod-\\<NL>down"` 真执行 prod-down（bash/zsh/sh/dash/ksh），
    `make $"prod-\\<NL>down"` bash/sh/ksh 真执行——POSIX 语义上引号不改变续
    接删除。round-4 只在未引用分支删了续接，加一对引号即可绕过；修法是把
    _segments 的三处 ``\\`` 分支收敛成一个（单引号区外的所有上下文成对处
    理、``\\<NL>`` 一律两字符全丢）。`sudo "ki\\<NL>ll"` 修前靠 sudo 空白词
    启发式碰巧拦，修后由续接统一处理稳定拦。"""

    def test_dquote_continuation_masks_word(self) -> None:
        assert blocked_shell(f'make "prod-{NL}down"')
        assert blocked_shell(f'docker "sto{NL}p" abc123')
        assert blocked_shell(f'docker compose "dow{NL}n"')
        assert blocked_shell(f'echo "$(make prod-{NL}down)"')

    def test_locale_quote_continuation_masks_word(self) -> None:
        assert blocked_shell(f'make $"prod-{NL}down"')
        assert blocked_shell(f'sudo $"ki{NL}ll" 1234')
        assert blocked_shell(f'echo $"$(make prod-{NL}down)"')

    def test_sudo_dquote_kill_now_stably_blocked(self) -> None:
        """`sudo "ki\\<NL>ll"` 修前 BLOCKED 是 sudo 空白词启发式的巧合（词面
        含换行触发 shell-text 重读）；修后续接先删，词面是干净的 kill——依
        赖的是 denylist 本身，不再依赖启发式。"""
        assert blocked_shell(f'sudo "ki{NL}ll" 1234')

    def test_exec_form_argv_is_prejoined(self) -> None:
        # exec 形态（ACP argv 预切分）：续接已由客户端拼好，word 即目标名。
        assert blocked_exec("make", ["prod-down"])


class TestSubstitutionBodyContinuations:
    """替换体（$()/backtick）内的 ``\\<newline>`` 续接（round-5 C1 同根
    因）：五 shell 实测 `echo $(make prod-\\<NL>down)`、`echo "$(pki\\<NL>ll
    uvicorn)"`、backtick 形态（echo "`make prod-\\<NL>down`"）、`echo $(make
    "prod-\\<NL>down")` 真执行——替换体不是引号，续接照删。round-4 修复时
    替换体分支显式排除换行（``\\``+``\\n`` 全进词内容），词被切开或带裸反
    斜杠而失配。"""

    def test_dollar_paren_body_continuation(self) -> None:
        assert blocked_shell(f"echo $(make prod-{NL}down)")
        assert blocked_shell(f'echo "$(pki{NL}ll uvicorn)"')
        assert blocked_shell(f"echo $(system{NL}ctl stop nginx)")

    def test_backtick_body_continuation(self) -> None:
        assert blocked_shell(f'echo "`make prod-{NL}down`"')

    def test_dquote_inside_substitution_body(self) -> None:
        assert blocked_shell(f'echo $(make "prod-{NL}down")')

    def test_escaped_construct_close_still_pairs(self) -> None:
        """收敛分支不得丢掉替换体内的成对转义（review #707 R3 回归对照）：
        ``\\)`` 不能关闭 ``$(``，载荷照拦；未闭合形态照旧 refused。"""
        assert blocked_shell('echo "$(x\\) ; make prod-down)"')
        assert blocked_shell('echo "$(make prod-down')


class TestLiteralQuoteContinuationsStayLiteral:
    """单引号与 ``$'…'``（ANSI-C）内的 ``\\<newline>`` 是字面数据（round-5
    C2 对照）：五 shell 实测 `make 'prod-\\<NL>down'` 与 `make $'prod-\\<NL>
    down'` 的目标词都含字面反斜杠/换行、不等于 prod-down，真实 make 找不到
    该目标——不拦是 shell 语义正确的（guard 按字面匹配即对，只确认不误
    报）。注意 `$"…"`（locale）不属于字面类：它拼回词面（C2 拦截族）。良
    性续接（拼回后无 denylist 内容）照常放行。"""

    def test_literal_forms_stay_allowed(self) -> None:
        assert not blocked_shell(f"make 'prod-{NL}down'")
        assert not blocked_shell(f"make $'prod-{NL}down'")

    def test_benign_continuations_stay_allowed(self) -> None:
        assert not blocked_shell(f'echo "ab{NL}cd" ; make check-quick')
        assert not blocked_shell(f'echo "ma{NL}ke check-quick"')
        assert not blocked_shell(f"echo $(make check-{NL}quick)")
        assert not blocked_shell(f'sudo $"ma{NL}ke" check-quick')


class TestXargsProcessSlotVar:
    """xargs --process-slot-var 是 required-argument（round-5 C3）：GNU
    findutils 4.9.0 实测（docker）裸形态必吃下一词——`xargs --process-slot-
    var v kill` 把 v 当变量名、kill 是 utility 真执行；round-4 曾把它与
    --max-lines 一起归为 optional 族排除出带值表，形成放行洞。与 --max-args
    同族而非 --max-lines 同族；BSD macos 无此选项（unrecognized，无对冲）。
    对照：`xargs --process-slot-var kill`（kill 被吃为变量名，默认 echo 输
    入）ALLOWED、合法 utility 照常放行、--max-lines 裸形态（真 optional）
    维持既有判定。"""

    def test_process_slot_var_eats_value_not_command(self) -> None:
        assert blocked_shell("xargs --process-slot-var v kill")
        assert blocked_shell("xargs --process-slot-var X make prod-down")
        assert blocked_shell("xargs --process-slot-var=v pkill uvicorn")
        # exec 形态（ACP argv 预切分）。
        assert blocked_exec("xargs", ["--process-slot-var", "v", "kill"])

    def test_benign_and_optional_forms_stay_correct(self) -> None:
        assert not blocked_shell("xargs --process-slot-var v make check-quick")
        assert not blocked_shell("xargs --process-slot-var kill")
        assert blocked_shell("xargs --max-lines kill")
        assert blocked_shell("xargs -i kill")
