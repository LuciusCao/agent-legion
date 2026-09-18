"""Round-4 attack payloads for the terminal guard (review #707 round 3).

Sister file of test_studio_chat_terminal_guard.py (same helpers, same
subsystem directory): P1-1 sudo -r/-t/--role/--type value options (SELinux
security context), P1-2 xargs value-taking options (-a reads the utility's
INPUT, not a replacement for it), P2 unquoted backslash-escaped separators
(``\\;`` is data). Split off at the 800-line threshold (AGENTS.md §4), same
convention as the round-3 sister file.
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


class TestSudoSelinuxValueOptions:
    """sudo -r/-t/--role/--type 带值选项（round-4 P1-1）：SELinux security
    context 形态 `sudo -r sysadm_r kill` / `sudo -t sysadm_t make prod-down`
    ——值是数据，值之后的词才是命令。实测：sudo 1.9.15p5（Linux，SELinux
    构建）两种形态均过选项解析到执行位；macOS 1.9.13p2 的拒绝原因是构建
    不含 -r/-t/--role/--type 选项（`sudo -h` 证实，SELinux 不构建——round-5
    H4 更正：不是簇合语义），同样到不了执行位。簇合取舍：`-rsysadm_r` 按
    GNU getopt 规则取簇尾为内联值（拦）；`-rt sysadm_t` 的簇尾两种读法都
    到不了执行位（Linux 1.9.15p5 认 -r 值 "t sysadm_t" 族、macOS 无该选
    项），不单测钉死该形态。"""

    def test_selinux_value_options_eat_value_not_command(self) -> None:
        assert blocked_shell("sudo -r sysadm_r kill 1234")
        assert blocked_shell("sudo -t sysadm_t make prod-down")
        assert blocked_shell("sudo -r sysadm_r -t sysadm_t make prod-down")
        assert blocked_shell("sudo --role sysadm_r kill 1234")
        assert blocked_shell("sudo --type sysadm_t kill 1234")
        assert blocked_shell("sudo --role=sysadm_r make prod-down")
        assert blocked_shell("sudo -rsysadm_r kill 1234")
        # exec 形态（ACP argv 预切分）。
        assert blocked_exec("sudo", ["-r", "sysadm_r", "kill", "1234"])
        assert blocked_exec("sudo", ["--type", "sysadm_t", "make", "prod-down"])

    def test_selinux_values_stay_data(self) -> None:
        """正确放行对照：值词是数据不是命令——`sudo -r kill kill 1234` 真实
        sudo 把第一个 kill 当 role（权限拒绝，不执行 kill）；值含分隔符文本
        同理。既有带值选项对照（-p 提示文本）不回归。"""
        assert not blocked_shell("sudo -r sysadm_r make check-quick")
        assert not blocked_shell("sudo --role sysadm_r make check-quick")
        assert not blocked_shell("sudo -p 'kill 1' make check-quick")


class TestXargsValueOptions:
    """xargs 带值选项（round-4 P1-2）：`xargs -a /tmp/pids kill` 的 kill 照
    常执行（-a 只是把 utility 的输入从 stdin 换成文件——GNU 4.9.0 实测 shim
    落地）；表缺 -a 曾把 /tmp/pids 当命令放行。实测补全：GNU 4.9.0 +
    BSD macos + worker 生产镜像同版本；-J/-R/-S（BSD 独有）与 -I/-L/-n/-P/
    -s/-E/-d 同表。**不进表**的取舍（GNU getopt optional-argument 实测）：
    -i/-l/-e 裸形态后一个词就是 utility（`xargs -i kill` 真执行 kill），列
    进表反而把 kill 吃成值放行；--replace/--eof/--max-lines 同因（`--max-
    lines kill` 真执行 kill）；-o/--open-tty 是布尔。round-5 C3 更正：
    --process-slot-var 是 required-argument（与 --max-args 同族，GNU 4.9.0
    实测裸形态必吃下一词），已入表，见 round-5 姊妹文件。"""

    def test_value_options_eat_value_not_command(self) -> None:
        assert blocked_shell("xargs -a /tmp/pids kill")
        assert blocked_shell("xargs --arg-file=/tmp/pids kill")
        assert blocked_shell("xargs --arg-file /tmp/pids kill")
        assert blocked_shell("xargs --max-args 2 kill")
        assert blocked_shell("xargs -L 2 kill")
        assert blocked_shell("xargs -L2 kill")
        assert blocked_shell("xargs -n 2 kill")
        assert blocked_shell("xargs -P 8 pkill uvicorn")
        assert blocked_shell("xargs -J % kill")
        assert blocked_shell("xargs -I {} -R 5 make prod-down")
        assert blocked_shell("xargs -s 4096 kill")
        assert blocked_shell("xargs -E END kill")
        # exec 形态（ACP argv 预切分）。
        assert blocked_exec("xargs", ["-a", "/tmp/pids", "kill"])
        assert blocked_exec("xargs", ["--arg-file=/tmp/pids", "kill"])

    def test_benign_xargs_usage_stays_allowed(self) -> None:
        """正确放行对照：合法 xargs 用法不误伤——`xargs -a file.txt echo`
        读取文件参数跑 echo；模板替换/并行等常规选项 + 非生命周期 utility。"""
        assert not blocked_shell("xargs -a file.txt echo")
        assert not blocked_shell("xargs -I {} echo {}")
        assert not blocked_shell("xargs -n 2 -P 8 make check-quick")
        assert not blocked_shell("cat x | xargs grep foo")

    def test_inline_optional_forms_run_next_word(self) -> None:
        """裸 optional-value 形态的后一个词 IS the utility（GNU 4.9.0 实测
        `xargs -i kill` / `xargs -o kill` / `xargs --max-lines kill` 真执行
        kill）——它们不进带值表，命令词照常落在 denylist 上。"""
        assert blocked_shell("xargs -i kill")
        assert blocked_shell("xargs -o kill")
        assert blocked_shell("xargs --max-lines kill")
        assert blocked_shell("xargs -t make prod-down")


class TestEscapedSeparatorsAreData:
    """未引用反斜杠转义的分隔符是数据（round-4 P2）：`echo safe\\; make
    prod-down` 五 shell 实测只执行 echo（输出字面 `safe; make prod-down`），
    但 _segments 曾在被转义的 ; 处切段把后半误判成真实命令。\\; \\| \\& 同
    族；\\<newline> 是行续接（五 shell 实测 $()/backtick/双引号内也删）；修
    夺融入 _segments 同一状态机（与未闭合检查共用 EOF 分支，未另开扫描）。"""

    def test_escaped_separators_do_not_split(self) -> None:
        assert not blocked_shell("echo safe\\; make prod-down")
        assert not blocked_shell("echo safe\\| make prod-down")
        assert not blocked_shell("echo safe\\& make prod-down")
        assert not blocked_shell("printf '%s' safe\\;kill 1234")
        # 良性转义文本（无 denylist 内容）本就放行。
        assert not blocked_shell("make check-quick\\; echo done")

    def test_unescaped_separators_still_split(self) -> None:
        """未转义分隔符照常切段：转义修复不得吞掉真实第二段——``\\|`` 惰性、
        `;` 照切；行续接拼回的 make prod-down 照拦。"""
        assert blocked_shell("echo safe ; make prod-down")
        assert blocked_shell("echo a\\|b ; make prod-down")
        assert blocked_shell("make prod-\\\ndown")
        assert blocked_shell("echo x \\& echo y ; make prod-down")

    def test_backslash_edge_forms(self) -> None:
        """边角形态：行尾孤立反斜杠不触发未闭合拦截（bash/dash 保留字面、
        zsh/sh/ksh 丢弃，五 shell 实测均无害）；单引号内的 `\\;` 是字面数据；
        引号内含分隔符文本照旧（P2 不动引号语义）。"""
        assert not blocked_shell("echo hi\\")
        assert not blocked_shell("echo 'safe; make prod-down'")
        assert not blocked_shell('echo "a\\" ; make prod-down"')
        assert blocked_shell("echo 'safe' ; make prod-down")
