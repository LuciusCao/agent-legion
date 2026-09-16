"""Service-lifecycle command denylist for Studio ACP terminals (issue #629).

Incident command: `make prod-down && make prod-up` executed via a chat agent
terminal; the session died in between and production stayed down. The guard
(terminal_guard.py) is a platform-level hard line layered over the permission
chain: matching is command-level (segment split + real-command identification
behind wrappers), so mentions inside strings/grep patterns never trigger.
"""

from __future__ import annotations

import pytest

from server.app.studio_chat.terminal_guard import ensure_terminal_command_allowed

pytestmark = pytest.mark.no_db


def blocked_exec(command: str, args: list[str] | None = None) -> bool:
    try:
        ensure_terminal_command_allowed(command, args)
    except Exception as exc:  # noqa: BLE001 - test helper, re-raised below
        assert "prod-restart.sh" in str(exc), "block message must point at the atomic entry"
        assert "issue #629" in str(exc), "block message must cite the incident"
        return True
    return False


def blocked_shell(text: str) -> bool:
    return blocked_exec("sh", ["-c", text])


# ---------------------------------------------------------------- blocked ----


class TestIncidentAndMakeForms:
    def test_incident_command_chain(self) -> None:
        """#629 原始事故命令：`make prod-down && make prod-up` 整链拒绝。"""
        assert blocked_shell("make prod-down && make prod-up")

    def test_make_targets_direct_and_prefixed(self) -> None:
        assert blocked_shell("make prod-down")
        assert blocked_shell("make prod-up")
        assert blocked_shell("make prod-down docker")
        assert blocked_shell("make dev-down")
        assert blocked_shell("make stack-host-down")
        # make 变量参数与多 target 形态
        assert blocked_shell("make -C /repo prod-down")
        assert blocked_shell("make prod-up prod-down")

    def test_make_target_at_any_position_of_a_chain(self) -> None:
        """prod-down 出现在链尾/链中/管道/子 shell 同样拒绝。"""
        assert blocked_shell("echo start; make prod-down")
        assert blocked_shell("make prod-down | tee /tmp/log")
        assert blocked_shell("(make prod-down)")

    def test_make_help_and_similar_targets_stay_allowed(self) -> None:
        assert not blocked_shell("make prod-up-help")
        assert not blocked_shell("make help")
        assert not blocked_shell("make check-quick")


class TestLifecycleScripts:
    def test_scripts_direct_by_basename_and_path(self) -> None:
        assert blocked_exec("./scripts/native-prod-down.sh")
        assert blocked_exec("scripts/native-prod-up.sh")
        assert blocked_exec(
            "/Users/x/GitHub/agent-legion/.worktrees/prod/scripts/native-prod-down.sh"
        )

    def test_scripts_via_sh_and_source(self) -> None:
        assert blocked_shell("sh scripts/native-prod-down.sh")
        assert blocked_shell("bash ./scripts/native-prod-down.sh")
        assert blocked_shell("source scripts/native-prod-down.sh")
        assert blocked_shell(". ./scripts/native-prod-down.sh")
        assert blocked_shell("bash -x scripts/native-prod-up.sh")
        # script invocation nested inside a larger shell command
        assert blocked_shell("cd scripts && ./native-prod-down.sh")


class TestProcessAndSystemCommands:
    def test_kill_family(self) -> None:
        assert blocked_shell("kill 1234")
        assert blocked_exec("kill", ["1234"])
        assert blocked_shell("pkill -f uvicorn")
        assert blocked_shell("killall python")
        assert blocked_shell("kill -9 99")
        # kill 内置无参数（合法裸命令形态）同样拒绝
        assert blocked_shell("kill")

    def test_launchctl_and_systemctl(self) -> None:
        assert blocked_shell("launchctl unload ~/Library/LaunchAgents/com.x.plist")
        assert blocked_shell("launchctl bootout gui/501/com.x")
        assert blocked_shell("systemctl stop agent-legion")

    def test_power_commands(self) -> None:
        assert blocked_shell("shutdown -h now")
        assert blocked_shell("sudo reboot")
        assert blocked_shell("halt")
        assert blocked_shell("poweroff")

    def test_docker_compose_and_brew_services(self) -> None:
        assert blocked_shell("docker compose -f deploy/compose.host.yaml down")
        assert blocked_shell("docker-compose down")
        assert blocked_shell("docker compose stop")
        assert blocked_shell("docker compose restart")
        # up 可重跑，不拦
        assert not blocked_shell("docker compose up -d --build")
        assert blocked_shell("brew services stop agent-legion")
        assert blocked_shell("brew services restart postgresql")


class TestEvasionForms:
    def test_wrappers_and_env_prefixes(self) -> None:
        assert blocked_shell("sudo make prod-down")
        assert blocked_shell("sudo -n kill 1234")
        assert blocked_shell("nice -n 10 make prod-down")
        assert blocked_shell("env -i launchctl list-x")
        assert blocked_shell("nohup make prod-down &")
        assert blocked_shell("timeout 60 make prod-down")
        assert blocked_shell("FOO=bar make prod-down")

    def test_nested_shell_and_eval(self) -> None:
        assert blocked_shell("bash -c 'make prod-down'")
        assert blocked_shell("bash -xc 'kill 1'")
        assert blocked_shell("bash -lc 'make prod-up'")
        assert blocked_shell("zsh -c 'sh -c \"pkill uvicorn\"'")
        assert blocked_shell("eval 'make prod-down'")

    def test_subshell_groups_and_command_substitution(self) -> None:
        assert blocked_shell("(make prod-down)")
        assert blocked_shell("(sleep 1; kill 99)")
        assert blocked_shell("echo $(make prod-down)")
        assert blocked_shell('echo "result: $(kill 99)"')
        assert blocked_shell('x="$(pkill uvicorn)"')

    def test_quote_concatenation(self) -> None:
        assert blocked_shell("make pr'od-down'")
        assert blocked_shell('make "prod-down"')
        assert blocked_shell("make prod-'down'")
        assert blocked_shell("ma''ke prod-down")

    def test_ansi_c_quoting(self) -> None:
        """ANSI-C quoting（`$'…'`）与普通引号一样只是拼接边界（review #629：
        反斜杠版 _unquote 会把 `$'` 原样留在词里导致 `make $'prod-down'`
        漏拦——denylist 全 ASCII 名，`$` 无歧义，直接并入剥离字符）。"""
        assert blocked_shell("make $'prod-down'")
        assert blocked_shell("ki$'ll' 1")
        assert blocked_shell("ma$'ke' prod-down")
        # $'…' 内的转义（\n、\t）不可能拼出这些纯 ASCII 名，不构成混淆面。
        assert blocked_shell("kill $'1'")


# ---------------------------------------------------------------- allowed ----


class TestFalsePositives:
    def test_string_and_grep_mentions_stay_allowed(self) -> None:
        assert not blocked_shell("echo 'prod-down'")
        assert not blocked_shell('echo "请人工执行 make prod-down"')
        assert not blocked_shell("grep -n prod-down Makefile")
        assert not blocked_shell("cat scripts/native-prod-down.sh")
        assert not blocked_shell("sed -n 1,20p scripts/native-prod-down.sh")

    def test_normal_development_commands_stay_allowed(self) -> None:
        assert not blocked_shell("make check-quick")
        assert not blocked_shell("ls -la")
        assert not blocked_shell("python -m pytest tests/services -q")
        assert not blocked_shell("git status && git diff")
        assert not blocked_shell("uv run pytest -q")
        assert not blocked_shell("curl -s http://127.0.0.1:8000/api/health")
        assert not blocked_shell("npm run build")

    def test_word_like_tokens_stay_allowed(self) -> None:
        assert not blocked_shell("echo shutdown-schedule killfile prod-downstream")
        assert not blocked_exec("echo", ["kill"])
        assert not blocked_exec("echo", ["launchctl", "is", "blocked", "text"])
        assert not blocked_exec("grep", ["-rn", "prod-down", "Makefile"])

    def test_docker_ps_and_logs_stay_allowed(self) -> None:
        assert not blocked_shell("docker compose ps")
        assert not blocked_shell("docker compose logs -f backend")
        assert not blocked_shell("docker ps")
        assert not blocked_shell("brew services list")

    def test_empty_and_assignment_segments(self) -> None:
        assert not blocked_shell("")
        assert not blocked_shell("   ")
        assert not blocked_shell("FOO=bar")
        assert not blocked_shell("echo hi")
        assert not blocked_exec("sh", [])


# ------------------------------------------------------- documented gaps -----


class TestDocumentedGaps:
    """已接受的绕过面（对齐 velites command_guard.rs 的口径）：变量拼接、
    make 变量传 target、`{ …; }` 组、stdin 喂脚本、exec/su/ssh 前缀。防御
    对象是「顺手做运维」的善意 agent，不是对抗性输入——对抗性场景由权限链
    与人在环审批兜底。"""

    def test_variable_and_brace_evasions(self) -> None:
        assert not blocked_shell("D=kill; $D 1")
        assert not blocked_shell("make T=prod-down $T")
        assert not blocked_shell("{ make prod-down; }")

    def test_stdin_fed_script_evasions(self) -> None:
        """shell 从 stdin 读脚本（无 -c 词可递归）：harmless-shaped data
        直通；这类形态与变量拼接同属已接受缺口，钉住现状防回归误判。"""
        assert not blocked_shell("echo 'kill 1' | bash")
        assert not blocked_shell("bash -s <<< 'make prod-down'")

    def test_exec_and_remote_evasions(self) -> None:
        assert not blocked_shell("exec make prod-down")
        assert not blocked_shell("su -c 'make prod-down' root")
        assert not blocked_shell("ssh localhost 'make prod-down'")
