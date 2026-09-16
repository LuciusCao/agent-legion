"""Service-lifecycle command guard for Studio ACP terminals (issue #629).

Incident: an agent ran ``make prod-down && make prod-up`` through its ACP
terminal; the session died between the two halves and production stayed down
until a human noticed. Principle from the issue: an agent may do anything
*reversible* — operations that cannot self-recover once interrupted belong to
a human at a real terminal.

Layering with the permission chain: the agent-side Bash tool is gated by
``session/request_permission`` BEFORE ``terminal/create`` (terminals.py), so
commands reaching this guard were in principle already approved. This guard
is a platform-level hard line ON TOP of that approval chain: the approving
surface (the chat UI served by the very backend these commands take down)
dies with the session, so chat-side approval can never make these commands
safe — they are refused unconditionally and the operator runs them in a real
terminal. ``scripts/prod-restart.sh`` is the atomic human entry for restarts
(down + up + health-check + failure retry), which the block message points
to. Same philosophy as velites' ``command_guard.rs`` (the footgun-guard
precedent): a heuristic over shell text, NOT a security boundary — an
adversarial agent can evade via variables (``D=kill; $D 1``) or command
substitution (``make $(echo prod-down)``); those gaps are accepted because
the realistic threat is a well-meaning agent doing ops casually.

Matching is command-level, never substring-level: text is split into shell
segments (``;`` ``|`` ``&`` ``&&`` newlines, parens and command
substitutions — quote-aware), each segment's real command is identified
behind wrappers/assignments (sudo/env/nice/timeout/xargs/…), and only that
command word (plus, for ``make``/``sh``/``source``/``docker``, their
operands) is compared against the denylist. ``echo "prod-down"`` and
``grep prod-down Makefile`` therefore stay allowed. Nested shells
(``bash -c '…'``, including short-option clusters like ``-lc``) and ``eval``
recurse into their command string. Known gaps: unresolved variables, brace
expansion, ``{ …; }`` groups, stdin-fed shells (``echo 'kill 1' | bash``,
``bash -s <<<'…'``), ``exec``/``su``/``ssh`` prefixes, and leading
redirections (``2>&1 cmd``) — mirroring the accepted-gap list of
command_guard.rs (the ANSI-C quoting gap there is closed here because this
denylist matches pure-ASCII names, unlike its path matching).
"""

from __future__ import annotations

from collections.abc import Iterator

# make targets that start/stop the platform's own services. Both halves of
# the pair are listed: interrupting `prod-down` leaves services down with
# nothing to bring them back (the session that ran it is gone — #629), and a
# half-finished `prod-up` is the same outage. The dev stack targets follow:
# the dev backend hosts the Studio session itself, so killing it kills the
# approving channel mid-flight. `stack-*` targets cover the Docker form.
BLOCKED_MAKE_TARGETS = frozenset(
    {
        "prod-up",
        "prod-down",
        "prod-restart",
        "stack-host-up",
        "stack-host-down",
        "stack-worker-up",
        "stack-worker-down",
        "stack-down",
        "dev-up",
        "dev-down",
    }
)

# Direct script invocations of the same lifecycle entry points, matched by
# basename so any worktree/path prefix (`./scripts/…`,
# `/path/to/.worktrees/prod/scripts/…`) is covered.
BLOCKED_SCRIPTS = frozenset(
    {
        "native-prod-up.sh",
        "native-prod-down.sh",
        "prod-restart.sh",
        "stack-prod-up.sh",
        "dev_stack.sh",
    }
)

# Process- and machine-level commands, refused unconditionally in the Studio
# terminal context. `kill`/`pkill`/`killall` have no legitimate target here:
# the agent's own child processes are managed by the terminal protocol itself
# (terminal/kill takes down the whole process group), so any raw kill aims at
# someone else's process — including the backend hosting this session.
# launchctl/systemctl (load/unload/bootout) and shutdown/reboot/halt/poweroff
# are service/host lifecycle by definition.
BLOCKED_COMMANDS = frozenset(
    {
        "kill",
        "pkill",
        "killall",
        "launchctl",
        "systemctl",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
    }
)

# Compose/launchd-family subcommands whose interruption leaves services off
# (down/stop/restart/kill take the stack down; `up` alone is rerunnable and
# stays allowed). `docker compose down` is the Docker prod-down; `brew
# services stop/restart/unload/kill` wraps launchctl on macOS.
BLOCKED_COMPOSE_SUBCOMMANDS = frozenset({"down", "stop", "restart", "kill"})
BLOCKED_BREW_SERVICES_SUBCOMMANDS = frozenset({"stop", "restart", "unload", "kill"})

# Leading wrapper tokens skipped when identifying the real command; their
# option flags (and value-taking flags) are skipped along the way.
WRAPPERS = frozenset(
    {"sudo", "command", "env", "time", "nice", "nohup", "setsid", "stdbuf", "xargs"}
)
# Shells whose `-c <string>` argument is a full shell command text (recursed
# into); without `-c` their file operands are script invocations (basename
# check). Combined short clusters (`-xc`, `-lc`) count as `-c`.
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})


class TerminalCommandBlockedError(Exception):
    """A terminal command hits the service-lifecycle denylist (issue #629)."""


_GUIDANCE = (
    "服务生命周期命令不属于 agent 会话：会话一旦中断就无法自恢复"
    "（生产后端曾因此停摆，issue #629），需要人工在宿主机终端执行。"
    "重启生产环境请用 ./scripts/prod-restart.sh"
    "（down+up+健康检查的原子入口，失败自动重试拉起）。"
)


def _blocked(what: str) -> TerminalCommandBlockedError:
    return TerminalCommandBlockedError(
        f"Studio terminal 拒绝执行服务生命周期命令（{what}）。{_GUIDANCE}"
    )


def ensure_terminal_command_allowed(command: str, args: list[str] | None) -> None:
    """Check the exec-form request (command + argv) before spawn; raises on deny."""
    _check_words([command, *(args or [])])


def _check_shell_text(text: str) -> None:
    for segment in _segments(text):
        words = segment.split()
        if words:
            _check_words(words)


def _check_words(words: list[str]) -> None:
    tokens = [_unquote(word) for word in words]
    identified = _identify(tokens)
    if identified is not None:
        _check_command(identified[0], identified[1])


def _check_command(cmd: str, args: list[str]) -> None:
    if cmd in BLOCKED_COMMANDS:
        raise _blocked(cmd)
    if cmd in BLOCKED_SCRIPTS:
        raise _blocked(cmd)
    if cmd == "make" and any(arg in BLOCKED_MAKE_TARGETS for arg in args):
        raise _blocked(f"make {next(arg for arg in args if arg in BLOCKED_MAKE_TARGETS)}")
    if cmd in SHELLS:
        position = next((i for i, arg in enumerate(args) if _is_shell_c_flag(arg)), None)
        if position is not None and position + 1 < len(args):
            # Recurse into the command text as the REMAINING args joined: a
            # quoted command string may have been split into several words by
            # the word-level unquoting (`bash -c 'make prod-down'`), and
            # join-restore approximates the original text. Over-blocks the
            # exotic case of later words being $0/$1 parameters — err safe.
            _check_shell_text(" ".join(args[position + 1 :]))
        else:
            _check_script_operands(cmd, args)
        return
    if cmd in ("source", "."):
        _check_script_operands(cmd, args)
        return
    if cmd == "eval":
        _check_shell_text(" ".join(args))
        return
    if cmd == "docker" and "compose" in args:
        if any(arg in BLOCKED_COMPOSE_SUBCOMMANDS for arg in args):
            raise _blocked("docker compose …")
        return
    if cmd == "docker-compose" and any(arg in BLOCKED_COMPOSE_SUBCOMMANDS for arg in args):
        raise _blocked("docker-compose …")
    if (
        cmd == "brew"
        and len(args) >= 2
        and args[0] == "services"
        and args[1] in BLOCKED_BREW_SERVICES_SUBCOMMANDS
    ):
        raise _blocked("brew services …")


def _check_script_operands(cmd: str, args: list[str]) -> None:
    for arg in args:
        if _basename(arg) in BLOCKED_SCRIPTS:
            raise _blocked(f"{cmd} … {arg}")


def _segments(command: str) -> Iterator[str]:
    """Split shell text into simple-command segments (quote-aware).

    Separators: ``;``, ``|``, ``&`` (covers ``&&``), newlines, subshell parens
    and command substitutions (``$(…)`` and backticks — both execute inside
    double quotes too, so they open segments there as well; single quotes
    suppress everything). Segment text keeps quotes for later word-level
    unquoting. Port of command_guard.rs's split_segments without the
    paren-depth cwd tracking (the denylist is depth-independent).
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    # Whether a paren opened inside double quotes (command substitution) —
    # the closing paren must restore the double-quote state.
    resume_double: list[bool] = []
    chars = iter(command)
    lookahead = None
    while True:
        char = lookahead or next(chars, None)
        lookahead = None
        if char is None:
            break
        if in_single:
            if char == "'":
                in_single = False
            current.append(char)
            continue
        if in_double:
            if char == '"':
                in_double = False
                current.append(char)
            elif char == "$":
                lookahead = next(chars, None)
                if lookahead == "(":
                    segments.append("".join(current).strip())
                    current = []
                    resume_double.append(True)
                    in_double = False
                    lookahead = None
                else:
                    current.append(char)
            elif char == "`":
                segments.append("".join(current).strip())
                current = []
                resume_double.append(True)
                in_double = False
            else:
                current.append(char)
            continue
        if char == "'":
            in_single = True
            current.append(char)
        elif char == '"':
            in_double = True
            current.append(char)
        elif char == "$":
            lookahead = next(chars, None)
            if lookahead == "(":
                segments.append("".join(current).strip())
                current = []
                resume_double.append(False)
                lookahead = None
            else:
                current.append(char)
        elif char == "(":
            segments.append("".join(current).strip())
            current = []
            resume_double.append(False)
        elif char == ")":
            segments.append("".join(current).strip())
            current = []
            if resume_double and resume_double.pop():
                in_double = True
        elif char in ("|", "&", ";", "`", "\n"):
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    segments.append("".join(current).strip())
    return (segment for segment in segments if segment)


def _unquote(token: str) -> str:
    """Strip quotes/escape backslashes: shell words concatenate quoted and
    unquoted parts (`pr"od-down"` is one word) and the quotes carry no command
    meaning for matching. `$'…'` (ANSI-C quoting) concatenates the same way;
    its escapes (`\\n`, `\\t`) cannot form these ASCII-only names, so the
    backslashes are dropped alongside the quote characters."""
    return "".join(char for char in token if char not in "\"'$\\")


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _is_assignment(token: str) -> bool:
    return (
        "=" in token
        and bool(token)
        and (token[0].isalpha() or token[0] == "_")
        and not token.startswith("-")
    )


def _is_shell_c_flag(token: str) -> bool:
    return token.startswith("-") and not token.startswith("--") and "c" in token[1:]


def _wrapper_value_opts(wrapper: str) -> tuple[str, ...]:
    """Option flags that consume the following token as their value, keyed by
    wrapper (`nice -n 10 …` vs `sudo -n …` — the same flag differs)."""
    mapping = {
        "sudo": ("-u", "-g", "-h"),
        "nice": ("-n",),
        "ionice": ("-n",),
        "env": ("-u", "-C", "-S"),
        "stdbuf": ("-i", "-o", "-e"),
        "xargs": ("-I", "-n", "-P", "-L", "-s", "-d"),
        "timeout": ("-k", "-s"),
    }
    return mapping.get(wrapper, ())


def _identify(tokens: list[str]) -> tuple[str, list[str]] | None:
    """The real command behind leading assignments and wrappers: returns
    (basename, args) or None for an assignment-only segment."""
    rest = tokens
    while rest:
        head = rest[0]
        if _is_assignment(head):
            rest = rest[1:]
        elif _basename(head) == "timeout":
            rest = _skip_options(rest[1:], _wrapper_value_opts("timeout"))
            if rest:
                rest = rest[1:]  # the duration positional
        elif _basename(head) in WRAPPERS:
            rest = _skip_options(rest[1:], _wrapper_value_opts(_basename(head)))
        else:
            return _basename(head), rest[1:]
    return None


def _skip_options(tokens: list[str], value_opts: tuple[str, ...]) -> list[str]:
    remaining = tokens
    while remaining and remaining[0].startswith("-"):
        takes_value = remaining[0] in value_opts
        remaining = remaining[1:]
        if takes_value and remaining:
            remaining = remaining[1:]
    return remaining
