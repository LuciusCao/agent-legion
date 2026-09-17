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

    def test_sudo_value_taking_options(self) -> None:
        """sudo 的带值选项（-p 提示文本、-D 目录等）消费下一参数——值是
        数据不是命令；漏登记会把提示文本当命令、漏掉后面的真实命令
        （review #629 P1）。清单来源：sudo 1.9.13p2 `sudo --help` / man
        OPTIONS，值消费形态 `sudo --host localhost -n true` 实测验证。"""
        # 短选项带值：值后仍能识别真实命令。
        assert blocked_shell("sudo -p 'Password: ' make prod-down")
        assert blocked_shell("sudo -D /repo make prod-down")
        assert blocked_shell("sudo -C 3 -T 30 -R / -U root -g wheel -u root -h h1 make prod-down")
        assert blocked_shell("sudo -u root kill 1234")
        assert blocked_shell("sudo -g wheel pkill uvicorn")
        # 长选项 = 赋值与分离赋值形态。
        assert blocked_shell("sudo --prompt=x make prod-down")
        assert blocked_shell("sudo --prompt 'x' make prod-down")
        assert blocked_shell("sudo --chdir /repo make prod-down")
        assert blocked_shell("sudo --chdir=/repo make prod-down")
        assert blocked_shell("sudo --host localhost --user root make prod-down")
        # 值本身是 denylist 词时不得误报为命中命令后的放行（-p 的值是
        # kill 只是提示文本，真实命令 make check 无害——整条应放行）。
        assert not blocked_shell("sudo -p 'kill' make check-quick")
        # 不带值的选项不受影响；-- 终止选项解析后照常识别。
        assert blocked_shell("sudo -n kill 1234")
        assert blocked_shell("sudo --preserve-env make prod-down")
        assert blocked_shell("sudo --user root -- kill 1234")

    def test_sudo_askpass_is_boolean(self) -> None:
        """--askpass 是布尔选项（sudo --help：`-A, --askpass  use a helper
        program for password prompting`，无值）——不得当带值选项消费下一词
        （review #629 P2-2：误登记曾让 `sudo --askpass make prod-down` 把
        make 当提示文本吃掉、把 prod-down 当命令而放行）。"""
        assert blocked_shell("sudo --askpass make prod-down")
        assert blocked_shell("sudo --askpass kill 1234")
        # 短形态 -A 一直是布尔，行为对照一致。
        assert blocked_shell("sudo -A make prod-down")
        # 与带值选项混排：--askpass 不消费，-u 照常消费 root。
        assert blocked_shell("sudo --askpass -u root kill 1234")

    def test_sudo_shell_mode_command_string(self) -> None:
        """sudo -s/-i/--shell/--login 的命令串经 $SHELL -c 执行（sudo
        --help："run shell as the target user; a command may also be
        specified"）——引用单词形态此前把带空格的词当命令名比对而放行
        （review #629 P2-3）；命中后剩余词 join 递归按 shell 文本解析。"""
        assert blocked_shell("sudo -s 'make prod-down'")
        assert blocked_shell("sudo -i 'kill 1'")
        assert blocked_shell("sudo --shell 'make prod-down'")
        assert blocked_shell("sudo --login 'kill 1'")
        # 未引用形态同样拦截（join 后递归，结论不变）。
        assert blocked_shell("sudo -s make prod-down")
        assert blocked_shell("sudo -i make prod-down")
        # 递归按 $SHELL -c 语义：串内未引用分隔符是命令边界，照常拦。
        assert blocked_shell("sudo -s 'echo safe; make prod-down'")
        # 选项区之后的 -s 才算：sudo kill -s 1 的 -s 属于 kill（本就拦）。
        assert blocked_shell("sudo -u root -s 'make prod-down'")
        # 数据参数不放大误伤：echo 的引用参数是一个惰性词。
        assert not blocked_shell("sudo -s echo 'kill 1'")

    def test_double_dash_terminator_stops_option_parsing(self) -> None:
        """`--` 终止选项解析：其后的词是命令/数据，不再当 sudo 选项消费
        （review #629 突变验证显示该分支零覆盖，钉住现状）。`sudo -- -u
        root kill` 的 -u 是命令名（真实 sudo 找不到名为 -u 的文件而失败，
        无害放行）；突变掉终止符分支会把 -u 当用户选项吃掉、误拦 kill。"""
        assert blocked_shell("sudo -- kill 1234")
        assert not blocked_shell("sudo -- -u root kill 1234")

    def test_double_quote_backslash_escape(self) -> None:
        """双引号内 `\\"` 是字面引号（POSIX），不提前闭合引号：`make "x\\"
        prod-down"` 的 target 是 `x" prod-down` 一个词（真实 make 报 no
        such target，无害放行）——review #629 突变验证显示该转义分支零
        覆盖。已知 fail-safe 误拦面（review P3-6，与 Rust 版同缺口、方向
        安全）：段切分器不认 `\\"`，`echo "foo\\"; make …"` 提前闭引号后
        把数据段误判为命令段。"""
        assert not blocked_shell('make "x\\" prod-down"')
        assert blocked_shell('echo "foo\\"; make prod-down"')

    def test_nested_shell_and_eval(self) -> None:
        assert blocked_shell("bash -c 'make prod-down'")
        assert blocked_shell("bash -xc 'kill 1'")
        assert blocked_shell("bash -lc 'make prod-up'")
        assert blocked_shell("zsh -c 'sh -c \"pkill uvicorn\"'")
        assert blocked_shell("eval 'make prod-down'")

    def test_nested_shell_quoted_payload_is_data(self) -> None:
        """shell -c 载荷的引号在递归解析前必须保留（review #629 P2-1）：
        去引号发生在按 shell 语义切词之后、逐词进行——载荷只是被引用的
        文本（echo 打印、赋值右值）时 `&& make prod-up` 等仍是数据；真实
        命令字符串（未加外层引用）照常递归拦截。"""
        assert not blocked_shell("sh -c \"echo 'make prod-down && make prod-up'\"")
        assert not blocked_shell("sh -c \"echo 'safe; make prod-down'\"")
        assert not blocked_shell("bash -c 'echo \"kill 1\"'")
        assert not blocked_shell("bash -c 'MSG=\"down: make prod-down\"; echo $MSG'")
        # 对照组：外层引号内是真实命令文本（内层引号只是拼词），仍拦。
        assert blocked_shell('sh -c "make prod-down"')
        assert blocked_shell("sh -c 'make prod-down && make prod-up'")
        assert blocked_shell("bash -c 'kill 1'")

    def test_quoted_separator_text_is_data(self) -> None:
        """顶层同理：含分隔符的引用文本是一个惰性词，不重切为命令
        （review #629 P2-1 的非嵌套面）。"""
        assert not blocked_shell("echo 'safe; make prod-down'")
        assert not blocked_shell('echo "run: make prod-down && make prod-up"')
        assert not blocked_shell("git commit -m 'fix: make prod-down docs'")
        # 对照组：未引用的分隔符仍是命令边界。
        assert blocked_shell("echo safe; make prod-down")

    def test_command_builtin_query_forms(self) -> None:
        """`command -v/-V/--help` 是只读查询（bash help command：只显示
        命令位置/描述，不执行）——放行（review #629 P2-2）；无查询选项的
        `command <cmd>` 是 wrapper 执行，照常检查；-p 是默认 PATH 执行，
        不在放行之列。"""
        assert not blocked_shell("command -v kill")
        assert not blocked_shell("command -V systemctl")
        assert not blocked_shell("command --help kill")
        assert not blocked_exec("command", ["-v", "kill"])
        assert not blocked_shell("sudo command -v kill")
        # 执行形态仍拦（含 wrapper 链与跨段）。
        assert blocked_shell("command kill")
        assert blocked_shell("command kill 1234")
        assert blocked_shell("command -p kill 1")
        assert blocked_shell("sudo command kill 1234")
        # 查询放行只作用于当前段：链中后续段的命令照常拦。
        assert blocked_shell("command -v kill; make prod-down")
        # 查询的目标词不因此放大误伤：-v 的参数是数据。
        assert not blocked_shell("command -v make")

    def test_command_query_flags_only_before_command_word(self) -> None:
        """`command` 自己的选项必须在命令词之前（bash `command [-pVv]
        name [arg …]`）；出现在目标命令参数里的标志属于目标——review
        #629 P2-1：任意位置匹配曾把 `docker compose down -v` 的卷删除
        标志、`brew services stop -v` 的 verbose 标志当查询标志放行。"""
        assert blocked_shell("command docker compose down -v")
        assert blocked_shell("command brew services stop -v postgresql")
        # 对照：查询标志在命令词之前（含多个前导选项）仍放行。
        assert not blocked_shell("command -v -p kill")

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
