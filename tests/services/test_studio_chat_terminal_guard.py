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
        覆盖。`echo "foo\\"; make prod-down"` 曾是登记的 fail-safe 误拦面
        （review P3-6：段切分器不认 `\\"` 提前闭引号）——round-3 起切分器
        认该转义，五 shell 实测整条是一个 echo（打印 `foo"; make
        prod-down`，make 不执行），判定随之与真实执行一致（放行）。"""
        assert not blocked_shell('make "x\\" prod-down"')
        assert not blocked_shell('echo "foo\\"; make prod-down"')

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


class TestCommandSubstitutionState:
    """双引号内命令替换（backtick / $()）后的解析器状态恢复（#707 攻击
    修复 CRITICAL-1）：backtick 替换的闭合不 pop 恢复栈时，宿主字符串的
    闭引号被误读为开引号，`; make prod-down` 整段被吞进引号词——任何
    denylist 条目都能这样绕过。"""

    def test_backtick_in_dquote_then_command(self) -> None:
        assert blocked_shell('echo "`x`" ; make prod-down')
        assert blocked_shell('true "`x`" ; kill 1234')
        assert blocked_shell('echo "`x`" ; launchctl bootout gui/501/com.x')
        assert blocked_shell('echo "`x`" ; docker compose down')
        # 最小形态：无空格直连。
        assert blocked_shell('"`x`";kill 1')
        # 多次 backtick 泄漏叠加同样恢复。
        assert blocked_shell('echo "`a`"; echo "`b`"; make prod-down')

    def test_backtick_state_recovers_across_substitutions(self) -> None:
        """栈在多个替换间正确配对：第二个 $() 的闭括号不得被 stale 栈
        误恢复成双引号态。"""
        assert blocked_shell('echo "$(echo $(x))"; kill 1')
        assert blocked_shell('echo "`x`" ; echo "$(y)" ; make prod-down')


class TestShellKeywordSyntax:
    """shell 关键词（if/while/until/!/coproc/{…}）占命令位时的识别
    （#707 攻击修复 CRITICAL-2）：关键词本身不是命令，跳过后同段内的
    真实命令照常进 denylist——`while ! make prod-up; do sleep 2; done`
    是 agent 自愈式重启的高频措辞。"""

    def test_condition_and_loop_keywords(self) -> None:
        assert blocked_shell("if make prod-down; then echo up; fi")
        assert blocked_shell("if kill 1234; then :; fi")
        assert blocked_shell("while make prod-down; do sleep 1; done")
        assert blocked_shell("while ! make prod-up; do sleep 2; done")
        assert blocked_shell("until launchctl list >/dev/null; do kill 1; done")
        assert blocked_shell("if [ -f x ]; then make prod-down; fi")

    def test_negation_and_coproc(self) -> None:
        assert blocked_shell("! make prod-down")
        assert blocked_shell("! kill 1234")
        assert blocked_shell("coproc make prod-down")

    def test_brace_group_is_not_a_gap_anymore(self) -> None:
        """`{ …; }` 组此前是登记缺口；关键词跳过集含 `{`/`}` 后组内
        命令照常识别（对照旧 TestDocumentedGaps 的反向断言）。"""
        assert blocked_shell("{ make prod-down; }")

    def test_keyword_like_words_stay_allowed(self) -> None:
        """误伤对照：这些词作为普通命令/数据不受关键词跳过影响。"""
        assert not blocked_shell("ifconfig")
        assert not blocked_shell("grep -n done report.txt")
        assert not blocked_shell("echo if then else fi while done")


class TestSudoClusteredShellFlags:
    """sudo 短选项簇合（`-Es` = -E + -s）的 shell 模式识别（#707 攻击修复
    CRITICAL-3）：簇内含 s/i 即为 shell 模式，剩余词按 $SHELL -c 命令串
    递归——对齐 bash `-xc` 簇合的既有先例。"""

    def test_clustered_shell_flags_with_quoted_string(self) -> None:
        assert blocked_shell("sudo -Es 'make prod-down'")
        assert blocked_shell("sudo -Es 'kill 1234'")
        assert blocked_shell("sudo -Ei 'kill 1'")
        assert blocked_shell("sudo -Ai 'make prod-down'")
        assert blocked_shell("sudo -Es 'docker compose down'")

    def test_cluster_without_shell_letter_still_direct(self) -> None:
        """不含 s/i 的簇（如 -En）不是 shell 模式：命令词照常直接比对。"""
        assert blocked_shell("sudo -En make prod-down")
        assert blocked_shell("sudo -kn kill 1234")


class TestTimeoutDurationShape:
    """timeout 的 duration 位置形态校验（#707 攻击修复 HIGH-4）：布尔长
    选项（--preserve-status）不得让真实命令被当 duration 丢掉。"""

    def test_boolean_long_options_do_not_shift_duration(self) -> None:
        assert blocked_shell("timeout --preserve-status make prod-down")
        assert blocked_shell("timeout --foreground make prod-down")
        assert blocked_exec("timeout", ["--preserve-status", "make", "prod-down"])

    def test_duration_forms_still_consumed(self) -> None:
        assert blocked_shell("timeout 60 make prod-down")
        assert blocked_shell("timeout 1d make prod-down")
        assert blocked_shell("timeout --kill-after=5 60 make prod-down")
        assert blocked_shell("timeout .5s kill 1234")


class TestMakeVariableOverrideForm:
    """`make target=value` 形态（#707 攻击修复 HIGH-5）：GNU make 把该词
    当命令行变量赋值后运行默认 target——`.DEFAULT_GOAL` 是生命周期 target
    时即真实停机（实测验证），匹配前剥掉 `=值` 后缀。"""

    def test_variable_override_form_matches_target(self) -> None:
        assert blocked_shell("make prod-down=1")
        assert blocked_shell("make VAR=x prod-down")


class TestDockerFirstLevelSubcommands:
    """docker 一级停机子命令（#707 攻击修复 HIGH-6）：stop/kill/restart/rm
    直拦（stack 形态容器逐个停掉等价 compose stop）；exec 的容器内命令递
    归检查；run 的宿主根挂载拦截。"""

    def test_container_lifecycle_subcommands(self) -> None:
        assert blocked_shell("docker stop agent-legion-backend")
        assert blocked_shell("docker kill agent-legion-backend")
        assert blocked_shell("docker restart agent-legion-backend")
        assert blocked_shell("docker rm -f agent-legion-backend")
        assert blocked_shell("docker stop $(docker ps -q)")
        assert blocked_exec("docker", ["stop", "backend"])
        # compose rm 此前漏拦（同族）。
        assert blocked_shell("docker compose rm -sf backend")

    def test_docker_exec_recurse_into_container_command(self) -> None:
        assert blocked_shell("docker exec agent-legion-backend make prod-down")
        assert blocked_shell("docker exec agent-legion-backend kill 1")
        assert blocked_shell("docker exec -u root backend launchctl list-x")
        assert blocked_exec("docker", ["exec", "c", "make", "prod-down"])
        # 容器内无害命令照常放行。
        assert not blocked_shell("docker exec backend ls -la")

    def test_docker_run_host_root_mount(self) -> None:
        assert blocked_shell("docker run -v /:/host alpine chroot /host sh -c 'make prod-down'")
        assert blocked_shell("docker run --volume /:/host alpine sh")
        assert blocked_shell("docker run -v /:/host --rm alpine true")
        # 数据卷挂载（非宿主根）不在拦截面。
        assert not blocked_shell("docker run -v /data:/data alpine ls")

    def test_docker_read_forms_stay_allowed(self) -> None:
        assert not blocked_shell("docker ps")
        assert not blocked_shell("docker logs backend")
        assert not blocked_shell("docker inspect backend")
        assert not blocked_shell("docker compose ps")


class TestRunnerRecursion:
    """高频 runner 的递归检查（#707 攻击修复 HIGH-7）：watch 与 find
    -exec 的载荷按命令行递归；其余 runner 家族（expect/script/parallel/
    at/解释器 -c）仍是登记缺口，不无限打地鼠。"""

    def test_watch_payload(self) -> None:
        assert blocked_shell("watch make prod-down")
        assert blocked_shell('watch -n 1 "kill 1234"')
        assert blocked_exec("watch", ["make", "prod-down"])
        assert not blocked_shell("watch -n 1 'ls -la'")

    def test_find_exec_payload(self) -> None:
        assert blocked_shell(r"find Makefile -maxdepth 0 -exec make prod-down \;")
        assert blocked_shell("find . -name Makefile -execdir make prod-down +")
        assert blocked_shell("find . -exec kill 1234 ;")
        assert blocked_exec("find", [".", "-exec", "make prod-down", ";"])
        assert not blocked_shell("find . -name '*.log' -delete")


class TestEnvOverrideInjection:
    """terminal/create 的 env 覆盖检查（#707 攻击修复 HIGH-4）：进程环境
    是 argv 之外的第二命令通道——BASH_ENV 在非交互 bash 启动时先 source
    指向的脚本（实测验证），PATH 空段让 CWD 影子命令解析。"""

    def test_injection_keys_are_refused(self) -> None:
        assert blocked_exec("bash", ["-c", "echo hi"], env=[("BASH_ENV", "/tmp/x.sh")])
        assert blocked_exec("sh", ["-c", "echo hi"], env=[("ENV", "/tmp/x.sh")])
        assert blocked_exec("bash", ["-c", "echo hi"], env=[("SHELLOPTS", "xtrace")])
        assert blocked_exec("bash", ["-c", "echo hi"], env=[("PROMPT_COMMAND", "kill 1")])

    def test_path_override_must_be_absolute_only(self) -> None:
        # 相对段/空段：可劫持命令解析，拒绝。
        assert blocked_exec("make", [], env=[("PATH", "/tmp/evil:.")])
        assert blocked_exec("make", [], env=[("PATH", ":/usr/bin")])
        assert blocked_exec("make", [], env=[("PATH", "/usr/bin::/bin")])
        # 纯绝对路径定制（加工具链目录）放行。
        assert not blocked_exec("make", ["check"], env=[("PATH", "/opt/toolchain:/usr/bin:/bin")])
        assert not blocked_exec("python3", ["-c", "print(1)"], env=[("LANG", "C.UTF-8")])

    def test_env_none_and_clean_pass(self) -> None:
        assert not blocked_exec("echo", ["hi"], env=None)
        assert not blocked_exec("echo", ["hi"], env=[("STUDIO_TEST_MARKER", "present")])


class TestEnvInjectionCommandLineForms:
    """env 注入的命令行形态（#707 复审 R1）：BLOCKED_ENV_NAMES 此前只在
    terminal/create 的 env 参数通道（_check_env）生效，同样的注入写成命令
    文本（赋值前缀 / env 参数 / sudo 透传 / export）完全放行——真实 bash 实
    测 ``BASH_ENV=x.sh bash -c 'echo AFTER'`` 先输出脚本内容再执行命令。"""

    def test_assignment_prefix_and_env_wrapper_forms(self) -> None:
        assert blocked_shell("BASH_ENV=/tmp/x.sh bash -c 'echo hi'")
        assert blocked_shell("env BASH_ENV=/tmp/x.sh bash -c 'echo hi'")
        assert blocked_shell("sudo BASH_ENV=/tmp/x.sh bash -c 'echo hi'")
        assert blocked_shell("ENV=/tmp/x.sh sh -c 'echo hi'")
        assert blocked_shell("env SHELLOPTS=xtrace bash -c 'echo hi'")
        assert blocked_shell("env PROMPT_COMMAND='kill 1' bash -i")

    def test_path_assignment_follows_same_rule(self) -> None:
        """PATH 的命令行形态沿用 _check_env 的全绝对段规则：相对段拒绝
        （同 `_path_override_allowed`），纯绝对定制放行——同一张表、同一语
        义，避免两通道判定漂移。"""
        assert blocked_shell("PATH=/tmp/evil:. make check")
        assert blocked_shell("env PATH=::/bin make check")
        assert blocked_shell("sudo PATH=.:/usr/bin make check")
        assert not blocked_shell("PATH=/opt/toolchain:/usr/bin:/bin make check-quick")
        assert not blocked_shell("env PATH=/opt/toolchain make check-quick")

    def test_export_form_of_injection_keys(self) -> None:
        """`export BASH_ENV=x; bash -c …` 是赋值前缀注入的两段孪生：导出的
        名字跨段存活、到达后续每个 shell。无值的 `export NAME`（重导出现值）
        不受影响。"""
        assert blocked_shell("export BASH_ENV=/tmp/x.sh; bash -c 'echo hi'")
        assert blocked_shell("export ENV=/tmp/x.sh; sh -c 'kill 1'")
        assert blocked_shell("export PATH=.:/bin; make check")
        assert not blocked_shell("export LANG=C.UTF-8; make check-quick")
        assert not blocked_shell("export PATH")

    def test_benign_assignments_stay_allowed(self) -> None:
        """普通变量赋值不放大误伤：合法 env 覆盖/前缀照常放行。"""
        assert not blocked_shell("env FOO=bar bash -c 'echo hi'")
        assert not blocked_shell("FOO=bar make check-quick")
        assert not blocked_shell("LC_ALL=C sort file.txt")

    def test_exec_form_assignment_prefix(self) -> None:
        """exec 形态：argv 前部的 BASH_ENV=… 词同样走注入判定（ACP argv
        预切分，赋值形态到达 _identify 时是独立 argv 词）。"""
        assert blocked_exec("BASH_ENV=/tmp/x.sh", ["bash", "-c", "echo hi"])
        assert blocked_exec("env", ["BASH_ENV=/tmp/x.sh", "bash", "-c", "echo hi"])


class TestSudoClusteredValueOptions:
    """sudo 簇合短选项 × 值选项复合形态（#707 复审 R2/R4）：`-su` 是 -s 加
    -u 取下一词（sudo 1.9 实测 `sudo -su` 报 "option requires an argument --
    u"），簇内值字母后跟字母则是内联值（`-us root` 实测 "unknown user s"，
    root 落命令位）。整 token 精确匹配的 _skip_options 曾把 root 当命令词
    放行 `sudo -su root 'make prod-down'`。"""

    def test_cluster_with_next_word_value(self) -> None:
        """值取下一词形态（值字母收尾）：真实 sudo = shell 模式 + -u root +
        命令，`sudo -su root make prod-down` 经 $SHELL -c 真实执行（sudo
        1.9 实测 `sudo -su` 报 "option requires an argument -- u" 证实簇合
        解析为 -s + -u）——活体路径。"""
        assert blocked_shell("sudo -su root make prod-down")
        assert blocked_shell("sudo -su root 'make prod-down'")
        assert blocked_shell("sudo -Esu root 'make prod-down'")
        assert blocked_shell("sudo -su root 'kill 1'")
        assert blocked_shell("sudo -su root kill 1234")
        # shell 模式串内的未引用分隔符照常分段（$SHELL -c 语义）。
        assert blocked_shell("sudo -su root 'echo hi; make prod-down'")

    def test_boolean_cluster_leaves_operand_as_command(self) -> None:
        """全布尔字母簇（-is = -i + -s，无值字母）：后续词都在命令位。
        `sudo -is root kill 1234` 真实是 $SHELL -c 'root kill 1234'——命令
        名 root（不存在），kill 只是参数、不执行；guard 同样把 root 当命令
        词、kill 留参数位，判定一致。串内真实执行的是首词后的独立段。"""
        assert not blocked_shell("sudo -is root kill 1234")
        assert not blocked_shell("sudo -is root 'make prod-down'")
        assert blocked_shell("sudo -is root 'echo x; make prod-down'")
        assert blocked_shell("sudo -is root; make prod-down")

    def test_inline_value_clusters_error_out(self) -> None:
        """值字母后跟字母（内联值）形态：真实 getopt 语义是值吃掉簇尾字母，
        后续词落命令位——`sudo -us root true` 实测报 "unknown user s"（root
        是命令名、true 是其参数）、`sudo -gs wheel …` 报 "unknown group s"，
        命令不执行。guard 按 sudo 相同的 getopt 解析定位命令词：root/wheel
        是命令词（非 denylist）放行＝真实报错不执行；denylist 词落到参数位
        （`-us root kill 1234`：命令是 root，kill 是参数）同样不构成执行。
        对照：分离形态 `sudo -u root kill` 的 kill 在命令位、照拦。"""
        assert blocked_shell("sudo -u root kill 1234")
        assert not blocked_shell("sudo -us root kill 1234")
        assert not blocked_shell("sudo -gs wheel kill 1234")
        assert not blocked_shell("sudo -hs myhost kill 1234")
        assert not blocked_shell("sudo -us root -s 'make prod-down'")
        assert not blocked_shell('sudo -ps "x" "make prod-down"')

    def test_prompt_cluster_value_is_data(self) -> None:
        """-p 的值（提示文本）是数据不是命令。`-ps "x" "make prod-down"`：
        -ps 的值是 s（getopt 内联），"x" 是命令名（不存在）、"make
        prod-down" 是其参数——真实 sudo 报 command not found，guard 放行
        与真实一致。对照：`-ps 'kill' make check-quick` 里 kill 落在命令位
        （真实 sudo 执行 kill make check-quick），照拦——簇合解析正确时值
        后的词就是命令。`sudo -p 'kill' make check`（分离形态，-p 消费提示
        文本、make 落命令位）保持放行。"""
        assert not blocked_shell("sudo -p 'kill' make check-quick")
        assert blocked_shell("sudo -ps 'kill' make check-quick")
        assert not blocked_shell('sudo -ps "x" "make prod-down"')

    def test_sudo_capital_s_quoted_string(self) -> None:
        """`sudo -S 'make prod-down'`（大写 S 读密码，非 shell 模式）：带引
        号命令串不可 execvp（fail-safe 按命令串递归拦截）；不带引号多词形
        态本就拦（命令词直接比对）。"""
        assert blocked_shell("sudo -S 'make prod-down'")
        assert blocked_shell("sudo -S make prod-down")
        assert blocked_shell("sudo -S 'kill 1'")
        assert blocked_shell("sudo -S -p 'x' 'docker compose down'")


class TestSubstitutionBackslashEscape:
    """命令替换体内的反斜杠转义（#707 复审 R3）：``$(`` 与 backtick 不是引
    号，替换体内 `\\` 转义任意下一字符——`\\)` 不是闭括号。此前 `_segments`
    在 `x\\)` 处提前闭段，其后的真实命令被吞进引号词放行；实测
    ``sh -c 'echo "$(x\\) ; touch /tmp/m)"'`` 真实执行 touch。"""

    def test_escaped_paren_stays_inside_substitution(self) -> None:
        assert blocked_shell('echo "$(x\\) ; make prod-down)"')
        assert blocked_shell('echo "$(x\\) ; kill 1234)"')
        # 替换体内转义 backtick 同族：真实 shell 报 unexpected EOF 后仍执行
        # 第二段命令（实测 marker 落地）。
        assert blocked_shell('echo "`x\\`y`" ; make prod-down')

    def test_unclosed_substitution_is_refused(self) -> None:
        """`\\)` 后无真闭括号的形态（`echo "$(x\\)" ; make prod-down`）：round-2
        曾按真实 bash 报 unexpected EOF 不执行而放行（翻转），round-3 收回——
        ksh 93u+ 对替换体内 `\\`` 的转义语义与 POSIX/bash **相反**（实测
        ``echo \\`x\\` ; cmd`` 在 ksh 真实执行第二段、其余四 shell parse
        error），guard 无法同时拟合两种语义，malformed 输入一律 fail-safe 拦
        截。取舍：宁可误拦几个 malformed 输入（用户重写命令的成本）也不放过
        ksh 的真实执行面；合法闭合替换体（`` `x` `` / `$(x)`）不受影响。"""
        assert blocked_shell('echo "$(x\\)" ; make prod-down')
        assert blocked_shell('echo "$(x\\)" ; kill 1')
        assert blocked_shell('echo "`x\\`" ; make prod-down')
        # ksh 实测活体形态（替换体内 `\\`` 转义歧义、宿主串随后开放到 EOF）。
        assert blocked_shell("echo `x\\` ; make prod-down")
        assert blocked_shell("echo `x\\` ; launchctl bootout gui/501/com.x")

    def test_backslash_escape_outside_substitutions_unchanged(self) -> None:
        """替换体外的转义语义不变：双引号内 `\\\\"` 是字面引号（POSIX）——
        `make "x\\" prod-down"` 与 `echo "foo\\"; make prod-down"` 都是合法
        闭合的单命令（五 shell 实测：make 不执行、echo 打印整串），放行与
        真实执行一致（P3-6 误拦面随 round-3 切分器认 `\\"` 而收口）。"""
        assert not blocked_shell('make "x\\" prod-down"')
        assert not blocked_shell('echo "foo\\"; make prod-down"')

    def test_closed_substitutions_still_recurse(self) -> None:
        """正常闭合的替换体（`` `x` `` / `$(x)` / `$(x\\\\)`）不落入未闭合拦
        截：替换体内的命令照常分段识别（CRITICAL-1 行为保持），双反斜杠（转义
        的反斜杠）后的真闭括号形态也不误伤。"""
        assert blocked_shell('echo "$(make prod-down)"')
        assert blocked_shell('echo "`make prod-down`"')
        assert blocked_shell('echo "`x`" ; make prod-down')
        assert blocked_shell('echo "$(x)" ; make prod-down')
        # `\\)` 后真闭括号的惰性形态（替换体无 denylist 内容）保持放行。
        assert not blocked_shell('echo "$(x\\))"')


class TestDockerMountHostRootForm:
    """docker run 的 --mount 宿主根挂载形态（#707 复审 R5）：`--mount
    type=bind,src=/,dst=…` 是宿主根挂载的另一拼写，与 -v 同拦。"""

    def test_mount_bind_host_root(self) -> None:
        assert blocked_shell(
            "docker run --mount type=bind,src=/,dst=/host alpine chroot /host sh -c 'make prod-down'"
        )
        assert blocked_shell("docker run --mount type=bind,source=/,target=/host alpine sh")
        # 内联 = 形态与只读修饰。
        assert blocked_shell("docker run --mount=type=bind,src=/,dst=/host alpine sh")
        assert blocked_shell("docker run --rm --mount type=bind,src=/,dst=/host,ro alpine true")

    def test_non_root_mounts_stay_allowed(self) -> None:
        assert not blocked_shell("docker run --mount type=bind,src=/data,dst=/data alpine ls")
        assert not blocked_shell("docker run --mount type=volume,src=x,dst=/x alpine sh")
        # dst-only：容器内同名路径，不触及宿主根。
        assert not blocked_shell("docker run --mount dst=/host alpine sh")
        assert not blocked_shell("docker run --mount tmpfs,dst=/tmp alpine sh")


class TestCompositeDurationForm:
    """timeout 复合时长（#707 复审 M1）：`1m30s` 是 coreutils 文档明确的合
    法复合形态（info coreutils "timeout invocation"）；此前不识别使 make 被
    当 duration 吞掉而放行。边界：`1m30`（尾段无单位）不是合法复合。"""

    def test_composite_duration_consumed(self) -> None:
        assert blocked_shell("timeout 1m30s make prod-down")
        assert blocked_shell("timeout 2h45m30s make prod-down")
        assert blocked_shell("timeout 1.5m kill 1234")
        assert not blocked_shell("timeout 1m30s make check-quick")

    def test_non_duration_words_stay_command_position(self) -> None:
        """非时长词不被当 duration 消费（HIGH-4 行为保持）。"""
        assert blocked_shell("timeout --preserve-status make prod-down")
        assert blocked_shell("timeout make prod-down")


class TestSegmentationAgreement:
    """分段器与切词器的空白集合对齐（#707 攻击修复 MEDIUM-9）：CR 在交互
    shell 是行结束符，分段器必须同样切分，不得让第二段命令藏在段内。"""

    def test_cr_separates_commands(self) -> None:
        assert blocked_shell("echo hi\rmake prod-down")
        assert blocked_shell("true\rkill 1")

    def test_recursion_depth_limit(self) -> None:
        """深嵌套以干净拒绝收口（#707 MEDIUM-10）：上限 64 层，超限不再
        RecursionError（SDK 侧 fail-closed 仍兜底，但干净 block 无栈噪音）。"""

        def nest(depth: int) -> str:
            text = "make prod-down"
            for i in range(depth):
                quote = "'" if i % 2 == 0 else '"'
                text = f"bash -c {quote}{text}{quote}"
            return text

        assert blocked_shell(nest(60))
        assert blocked_shell(nest(100))
        assert blocked_shell(nest(331))  # 原触发 RecursionError 的层数


# ------------------------------------------------------- documented gaps -----


class TestDocumentedGaps:
    """已接受的绕过面（对齐 velites command_guard.rs 的口径）：变量拼接、
    make 变量传 target、`{ …; }` 组、stdin 喂脚本、exec/su/ssh 前缀。防御
    对象是「顺手做运维」的善意 agent，不是对抗性输入——对抗性场景由权限链
    与人在环审批兜底。"""

    def test_variable_evasions(self) -> None:
        """已接受的绕过面（对齐 velites command_guard.rs 的口径）：变量拼接、
        make 变量传 target、stdin 喂脚本、exec/su/ssh 前缀。防御对象是
        「顺手做运维」的善意 agent，不是对抗性输入——对抗性场景由权限链
        与人在环审批兜底。注意 `{ make prod-down; }` 不再是缺口：`{`/`}`
        是 shell 关键词（#707 攻击修复把关键词纳入跳过集后，组内命令
        照常识别），该形态移入 TestShellKeywordSyntax。"""
        assert not blocked_shell("D=kill; $D 1")
        assert not blocked_shell("make T=prod-down $T")

    def test_stdin_fed_script_evasions(self) -> None:
        """shell 从 stdin 读脚本（无 -c 词可递归）：harmless-shaped data
        直通；这类形态与变量拼接同属已接受缺口，钉住现状防回归误判。"""
        assert not blocked_shell("echo 'kill 1' | bash")
        assert not blocked_shell("bash -s <<< 'make prod-down'")

    def test_exec_and_remote_evasions(self) -> None:
        assert not blocked_shell("exec make prod-down")
        assert not blocked_shell("su -c 'make prod-down' root")
        assert not blocked_shell("ssh localhost 'make prod-down'")
