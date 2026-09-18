"""Round-8 attack payloads for the terminal guard (review #707 round 7).

Sister file of test_studio_chat_terminal_guard_round7.py (same helpers,
same subsystem directory): C1' the case-pattern ``)`` that PIERCED the
round-7 frame stack (the shared closer popped a backtick frame mid-span,
so the payload after ``case x in *)`` was re-lexed at top level while all
five shells ran the joined ``make prod-down``), M1' the benign
case-in-backtick family the same bug mis-blocked, M2' the EOF-unclosed
ksh window it opened, plus M3' the ionice PID-mode flag spellings. Split
off at the 800-line threshold (AGENTS.md §4), same convention as
round-3..7.
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


class TestCaseCloserBypass:
    """case 模式分隔符 ``)`` 打穿替换体帧栈（round-8 C1'）：backtick span
    的真实终点只有未转义的 `` ` ``，体内 ``)``/引号/``;`` 全是 span 文本
    （五 shell 一致）；round-7 的 ``)`` 与关闭反引号共用 pop 分支让 ``case x in
    *)`` 把帧中途弹掉，剩余文本按顶层字面规则重词法——``'prod-\\<NL>down'``
    不再拼接、kill 词被引号切碎，guard 放行而五 shell（含 bash3）真实执
    行。修法：``)`` 只关 ``$(…)``/paren 帧，backtick 帧只在未转义 `` ` ``
    处弹出。修复用 case 宿主形态复现 round-7 C1 的续接语义（载荷必须再
    补一个良性 case 使整条命令在真实 shell 语法合法，攻击成本为零）。"""

    def test_case_closer_bypass_family_blocked(self) -> None:
        # 复审 V1/V9/X1/X5/X4 触发面：宿主与修复 case 的四种组合。
        assert blocked_shell(
            f"echo `case y in *) make 'prod-{NL}down';; esac` ; case z in *) echo hi;; esac"
        )
        assert blocked_shell(
            f"X=`case y in *) make 'prod-{NL}down';; esac` ; case z in *) true;; esac"
        )
        assert blocked_shell(
            f"echo `case y in *) make 'prod-{NL}down';; esac` && case z in *) true;; esac"
        )
        assert blocked_shell(
            f"echo `case y in *) make 'prod-{NL}down';; esac`\ncase z in *) true;; esac"
        )
        # 摆荡形态（X4/Y9）：良性 case-bt span 与载荷 span 交错。
        assert blocked_shell(
            f"echo `case y in *) true;; esac` ; echo `make 'prod-{NL}down'`"
            " ; case z in *) true;; esac"
        )
        assert blocked_shell(
            f"echo `case y in *) true;; esac` ; echo `make 'prod-{NL}down'`"
            " ; echo `case w in *) true;; esac`"
        )

    def test_quoted_command_word_forms_blocked(self) -> None:
        # 命令词本身被引号+续接切碎的形态（V4/V4c）：真实 shell 执行
        # kill/sudo，guard 修前把引号区按顶层字面读成残词放行。
        assert blocked_shell(
            f"echo `case y in *) 'ki{NL}ll' 1234;; esac` ; case z in *) echo hi;; esac"
        )
        assert blocked_shell(
            f"echo `case y in *) 'su{NL}do' kill 1;; esac` ; case z in *) true;; esac"
        )
        # 整词 kill 直接落在 case arm 里（无需续接）与双引号词变体。
        assert blocked_shell("echo `case y in *) kill 1234;; esac` ; case z in *) true;; esac")
        assert blocked_shell(
            f'echo `case y in *) make "prod-{NL}down";; esac` ; case z in *) true;; esac'
        )

    def test_nested_hosts_and_pattern_shapes_blocked(self) -> None:
        # $() 宿主（Z1）、空 pattern（Z5b）、跨行 case（Z6）、(a) 带括号
        # pattern（Y5：真实 shell pattern 不匹配不执行，拦截方向无害）。
        assert blocked_shell(f"echo $(x `case y in *) make 'prod-{NL}down';; esac`)")
        assert blocked_shell(f"echo `case '' in '') make 'prod-{NL}down';; esac`")
        assert blocked_shell(f"echo `case y in\n*) make 'prod-{NL}down';;\nesac`")
        assert blocked_shell(
            f"echo `case y in (a) make 'prod-{NL}down';; esac` ; case z in *) true;; esac"
        )


class TestCaseCloserBenignFamily:
    """良性 case-in-backtick 全族（round-8 M1'，round-7 误拦回归）：case
    是 agent 日常 shell 写法，``)`` 错误弹帧后 ``esac`` 尾部的 `` ` `` 在
    空/非 backtick 栈顶被当开体 push，EOF 栈残留触发 round-3 H1 未闭合
    拒绝——五 shell 全部正常执行。span-scan（``)`` 不关 backtick 帧）同
    时消除该族误拦。"""

    def test_benign_case_in_backtick_allowed(self) -> None:
        # G1-G4/G7 宿主全家 + 顶层 case（G6）与 $() 体（G5，Y4/Y8 pin
        # 所在族）对照。
        assert not blocked_shell("echo `case y in *) echo arm;; esac`")
        assert not blocked_shell("X=`case y in *) echo arm;; esac`")
        assert not blocked_shell('echo "`case y in *) echo arm;; esac`"')
        assert not blocked_shell("echo $(x `case y in *) echo arm;; esac`)")
        assert not blocked_shell(
            "echo `case y in *) echo arm;; esac` ; echo `case w in *) echo arm2;; esac`"
        )
        assert not blocked_shell("case y in *) echo arm;; esac")

    def test_balanced_paren_bodies_still_allowed(self) -> None:
        # 括号平衡的 subshell/函数定义体不受影响（F2/F3 对照）。
        assert not blocked_shell("echo `(cd /tmp && pwd)`")
        assert not blocked_shell("echo `f() { echo hi; }`")


class TestUnclosedCaseBacktick:
    """EOF 未闭合 backtick + case-``)``（round-8 M2'）：修前 ``)`` 把唯一
    帧弹光，EOF 栈空绕过 round-3 H1 未闭合拒绝——ksh 93u+ 真实执行拼接
    载荷（四 POSIX shell parse error）。span-scan 下 backtick 帧只能在
    `` ` `` 处弹出，EOF 栈非空恒触发 H1（错误消息逐字不变）。"""

    def test_unclosed_case_backtick_refused(self) -> None:
        # K1/K5（续接形态）与 K2（整词 kill）全部拒绝。
        assert blocked_shell(f"echo `case y in *) make 'prod-{NL}down';; esac")
        assert blocked_shell(f"X=`case y in *) make 'prod-{NL}down';; esac")
        assert blocked_shell("echo `case y in *) kill 1234;; esac")


class TestIonicePidModes:
    """ionice 作用于已运行进程的模式旗标（round-8 M3'）：``-p/-P/-u``
    的黏连（``-p123``）与分离（``-p 123``）拼写必须同判——真实
    util-linux 2.38 对两种拼写都进入 PID/PGID/UID 模式，多余位置词按
    进程号解析报错（``ioprio_get failed``），不执行任何命令；修前黏连
    形态被当布尔短选项剥掉、pkill 落命令位误拦，与分离形态判定相反。
    ``--t`` 是 ``--ignore`` 的唯一 GNU 前缀缩写且为布尔——当前 BLOCK 恰
    好 correct，钉死防止未来「补全」值表把 kill 喂给旗标值反向引入绕
    过（见 _IONICE_PID_MODE 注释）。"""

    def test_pid_mode_spellings_stay_allowed(self) -> None:
        # 黏连与分离、短与长、inline 形态全部 ALLOW（真实报错不执行）。
        assert not blocked_exec("ionice", ["-p123", "kill"])
        assert not blocked_exec("ionice", ["-u1000", "kill"])
        assert not blocked_exec("ionice", ["-P5", "kill"])
        assert not blocked_exec("ionice", ["-p", "123", "kill"])
        assert not blocked_exec("ionice", ["-u", "1000", "kill"])
        assert not blocked_exec("ionice", ["-P", "5", "kill"])
        assert not blocked_shell("ionice -p123 kill 1234")
        assert not blocked_shell("ionice -u 1000 kill")
        assert not blocked_shell("ionice -p123 make check-quick")

    def test_pid_mode_does_not_mask_value_flags(self) -> None:
        # PID 模式旗标只清其后命令位：普通值旗标路径的拦截不受影响
        # （round-7 M1 修复本体维持）。
        assert blocked_exec("ionice", ["kill", "1234"])
        assert blocked_exec("ionice", ["-n", "7", "pkill", "-f", "uvicorn"])
        assert blocked_exec("ionice", ["-c2", "-n7", "systemctl", "stop", "docker"])
        assert blocked_shell("ionice --class 2 --classdata 7 make prod-down")
        assert not blocked_exec("ionice", ["-n", "7", "make", "check-quick"])

    def test_boolean_ignore_abbreviation_stays_blocked(self) -> None:
        # --t/--i/--ig 都是 --ignore 的唯一前缀（布尔）：kill 落命令位
        # BLOCK 与真实执行一致——防止未来把 --ignore 系登记为吃值。
        assert blocked_shell("ionice --t kill")
        assert blocked_shell("ionice --i kill")
        assert blocked_shell("ionice --ig kill")
        assert blocked_shell("ionice -t kill")
