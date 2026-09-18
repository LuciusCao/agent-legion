"""Round-6 attack payloads for the terminal guard (review #707 round 5).

Sister file of test_studio_chat_terminal_guard.py (same helpers, same
subsystem directory): H1 GNU getopt long-option ABBREVIATIONS (a unique
unambiguous prefix of a value-taking long option consumes the next word —
`xargs --p v kill` really ran kill on GNU 4.9.0), M2/M4 the unified
backslash branch's single-quote rule inside command-substitution bodies
(round-5's condition OR-ed resume_double in, misreading `'…'` regions
inside $(…) bodies), plus the watch//usr/bin/time table corrections the
live re-testing surfaced. Split off at the 800-line threshold (AGENTS.md
§4), same convention as the round-3/4/5 sister files.
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


class TestLongOptionAbbreviations:
    """GNU getopt 长选项唯一前缀缩写（round-6 H1）：无歧义前缀等价全拼
    ——`xargs --p v kill`、`--pr v kill` 乃至内联 `--pro=v kill` 在 GNU
    findutils 4.9.0 真执行 kill（docker 全前缀矩阵：96 个前缀里 90 个吃
    值，6 个歧义报错不执行），round-5 的全 token 精确匹配对每个缩写形态
    放行。修法：_skip_options 的长选项路径按 GNU 语义做“恰好一个条目的
    前缀”匹配（歧义=不匹配=保守方向）。BSD/macOS 无缩写（unrecognized
    不执行），方向无害。值显式报错的形态（--arg-f/--max-args 的值是文
    件名/数字）同样拦截：值后落命令位的分析与真实工具一致，只是真实工
    具此刻报错而已。"""

    def test_process_slot_var_abbreviations_eat_value(self) -> None:
        assert blocked_shell("printf '1\\n2\\n' | xargs --p v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --pr v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --pro v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --process v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --process-slot v kill")
        # 内联缩写（GNU 接受缩写 + 内联值组合）。
        assert blocked_shell("printf '1\\n2\\n' | xargs --pro=v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --p=v kill")
        # exec 形态（ACP argv 预切分）与 make 载荷。
        assert blocked_exec("xargs", ["--pro", "v", "kill"])
        assert blocked_shell("xargs --pro v make prod-down")

    def test_other_xargs_value_families_covered(self) -> None:
        assert blocked_shell("printf '1\\n2\\n' | xargs --d v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --deli v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --d=: kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --max-a v kill")
        assert blocked_shell("printf '1\\n2\\n' | xargs --ar v kill")

    def test_ambiguous_and_exit_options_stay_allowed(self) -> None:
        """歧义前缀（GNU 报错不执行）与 --help/--version（GNU 打印即退）
        保持放行：真实工具不执行任何命令，放行与真实一致。"""
        assert not blocked_shell("printf '1\\n2\\n' | xargs --e v kill")
        assert not blocked_shell("printf '1\\n2\\n' | xargs --m v kill")
        assert not blocked_shell("printf '1\\n2\\n' | xargs --max v kill")
        assert not blocked_shell("xargs --help v kill")
        assert not blocked_shell("xargs --vers v kill")

    def test_other_gnu_wrappers_abbreviations(self) -> None:
        """env/timeout/nice/sudo 的 GNU 同族缩写（coreutils 9.1 docker 实
        测 / timeout 本机 GNU 9.5 实测吃值）。timeout 的长选项带值表
        （--signal/--kill-after）为 round-6 补齐。"""
        assert blocked_shell("env --uns FOO kill 1")
        assert blocked_shell("env --u FOO kill 1")
        assert blocked_shell("env --ch /tmp kill 1")
        assert blocked_shell("env --sp 'X=1' kill 1")
        assert blocked_shell("timeout --sig KILL 10 kill 1")
        assert blocked_shell("timeout --s KILL 10 kill 1")
        assert blocked_shell("timeout --ki 5 10 kill 1")
        assert blocked_shell("nice --adj 10 make prod-down")
        assert blocked_shell("nice --a 10 make prod-down")
        assert blocked_shell("sudo --use root kill 1234")
        assert blocked_shell("sudo --rol sysadm_r kill 1234")

    def test_benign_abbreviated_usage_stays_allowed(self) -> None:
        """合法缩写用法不误伤：值后的真实命令是非生命周期命令。"""
        assert not blocked_shell("xargs --pro v make check-quick")
        assert not blocked_shell("env --uns FOO make check-quick")
        assert not blocked_shell("timeout --sig KILL 10 make check-quick")


class TestSubstitutionBodySingleQuotes:
    """替换体内单引号区的字面语义（round-6 M2/M4）——仅对 ``$(…)`` 体成
    立：替换体不可能在单引号区内打开（单引号吞到闭合为止），体内看到的
    ``'`` 是全新引用上下文，五 shell 实测 `echo "$(make 'prod-\\<NL>down')"`
    的 make 目标含字面 ``\\``+NL、不执行（round-5 误拦 M2）。backtick 体
    是例外（round-7 C1：体内按 backtick 词法重解析，``\\<NL>`` 在 ``'…'``
    区内也是续接，五 shell 真执行），其形态移入 round-7 姊妹文件的拦截
    断言。而 ``'a\\'`` 的 ``\\'`` 在替换体内是转义的引号（五 shell 实测
    marker+od 复核：kill 真执行——复审报告“仅 ksh 执行”系误报），修前
    guard 吞掉引号闭合把 kill 藏进词尾放行（M4）。修法：统一分支条件去
    掉 resume_double 合取、单看 in_single（它天然按替换体作用域），round-7
    再给 backtick 体单独开续接通道。"""

    def test_single_quoted_literal_in_substitution_stays_allowed(self) -> None:
        # $() bodies only (round-6 M2, five shells keep the literal). The
        # backtick twin on the third line RUNS the joined target (round-7
        # C1) and moved to the round-7 file's blocked assertions.
        assert not blocked_shell(f"echo \"$(make 'prod-{NL}down')\"")
        assert not blocked_shell(f"echo $(make 'prod-{NL}down')")
        assert not blocked_shell(f"echo \"$(make 'ch{NL}eck')\" ; true")

    def test_escaped_quote_in_substitution_runs_command(self) -> None:
        assert blocked_shell("echo \"$(echo 'a\\' ; kill 1234)' )\"")
        assert blocked_shell("echo \"$(echo 'a\\' ; make prod-down)' )\"")
        # 良性同构体（第二命令无害）保持放行。
        assert not blocked_shell("echo \"$(echo 'a\\' ; true)' )\"")


class TestWatchAndTimeTables:
    """round-6 表项更正（live 复测）：watch 只有 -n/--interval 吃值，
    -g/-p/-t/-x/-l 等全是布尔旗标（procps 4.0.2/4.0.4 实测 `watch -g
    make prod-down` 每 interval 真跑 make prod-down）——round-1..5 把
    它们错登记为带值，`watch -g make prod-down` 曾把 make 喂给旗标而放
    行。/usr/bin/time（GNU time 1.9）的 -f/-o/--format/--output 带值
    （GNU 实测执行载荷；BSD time 的 -f/-v 不识别报错、-o/-l 布尔——
    BSD 侧同为 make 落命令位，方向一致）。"""

    def test_watch_flags_do_not_eat_the_command(self) -> None:
        assert blocked_shell("watch -g make prod-down")
        assert blocked_shell("watch -p make prod-down")
        assert blocked_shell("watch -t make prod-down")
        assert blocked_shell("watch -l make prod-down")
        assert blocked_shell("watch --i 1 make prod-down")
        # -n 的值照常被吃（旧行为保持）。
        assert blocked_shell('watch -n 1 "kill 1234"')
        assert not blocked_shell("watch -n 1 'ls -la'")

    def test_usr_bin_time_value_options(self) -> None:
        assert blocked_shell("/usr/bin/time -f 'x' make prod-down")
        assert blocked_shell("/usr/bin/time -o /tmp/t.txt make prod-down")
        assert blocked_shell("/usr/bin/time --fo 'x' make prod-down")
        assert blocked_shell("/usr/bin/time --out /tmp/t.txt make prod-down")
