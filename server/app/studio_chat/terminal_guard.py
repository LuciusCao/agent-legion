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
substitutions — quote-aware), each segment is split into words by shell
semantics and quote-removed per word, the real command is identified
behind wrappers/assignments (sudo/env/nice/timeout/xargs/…), and only that
command word (plus, for ``make``/``sh``/``source``/``docker``, their
operands) is compared against the denylist. ``echo "prod-down"`` and
``grep prod-down Makefile`` therefore stay allowed — and because quote
removal happens per word AFTER the split, quoted text containing
separators (``echo 'safe; make prod-down'``, a ``-c`` payload that merely
prints lifecycle names) stays data. Nested shells (``bash -c '…'``,
including short-option clusters like ``-lc``) and ``eval`` recurse into
their command string with its inner quoting intact. Known gaps: unresolved
variables, brace expansion, ``{ …; }`` groups, stdin-fed shells (``echo
'kill 1' | bash``, ``bash -s <<<'…'``), ``env -S 'VAR=x cmd …'`` (the
string is re-parsed by env itself, not recursed into), backslash-newline
continuations (``make \\<newline>prod-down``), heredoc bodies, ``exec``/
``su``/``ssh`` prefixes, and leading redirections (``2>&1 cmd``) —
mirroring the accepted-gap list of command_guard.rs (the ANSI-C quoting
gap there is closed here because this denylist matches pure-ASCII names,
unlike its path matching).
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import takewhile

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
# option flags (and value-taking flags) are skipped along the way. `command`
# is NOT here: it is a shell builtin with read-only query modes (-v/-V),
# handled in _check_command instead of peeled blindly.
WRAPPERS = frozenset({"sudo", "env", "time", "nice", "nohup", "setsid", "stdbuf", "xargs"})
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
        words = _shell_split_words(segment)
        if words:
            _check_words(words)


def _check_words(words: list[str]) -> None:
    # _shell_split_words (or exec-form argv, which arrives pre-split and
    # pre-unquoted by the ACP client) is the ONLY place quote removal may
    # happen: unquoting must follow word splitting, never precede it, so a
    # quoted word containing separators (``echo 'safe; make prod-down'``)
    # stays one inert word instead of being re-parsed as commands.
    identified = _identify(words)
    if identified is not None:
        _check_command(identified[0], identified[1])


def _check_command(cmd: str, args: list[str]) -> None:
    if cmd in BLOCKED_COMMANDS:
        raise _blocked(cmd)
    if cmd in BLOCKED_SCRIPTS:
        raise _blocked(cmd)
    if cmd == "command":
        # `command -v kill` / `command -V systemctl` only report where the
        # command lives — a read-only query, not an execution (review #629).
        # -p executes via a default PATH and must NOT be exempt. The builtin
        # is not in WRAPPERS on purpose: it is handled here, after its query
        # modes are known, instead of being peeled blindly in _identify.
        if _is_command_query(args):
            return
        rest = _skip_options(args, ())
        if rest:
            _check_command(_basename(rest[0]), rest[1:])
        return
    if cmd == "make" and any(arg in BLOCKED_MAKE_TARGETS for arg in args):
        raise _blocked(f"make {next(arg for arg in args if arg in BLOCKED_MAKE_TARGETS)}")
    if cmd in SHELLS:
        position = next((i for i, arg in enumerate(args) if _is_shell_c_flag(arg)), None)
        if position is not None and position + 1 < len(args):
            # Recurse into the command text as the REMAINING args joined: an
            # exec-form argv (`bash -c make prod-down`, arriving pre-split
            # with no shell reassembly on record) is best approximated by the
            # join — over-blocking the exotic case of later words being
            # positional parameters. A shell-text `bash -c '…'` arrives as ONE
            # word (inner quotes intact, _shell_split_words keeps them), so
            # the recursion here receives the real text.
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


# `command` builtin forms that only report information and never execute the
# target: -v prints the path/filename, -V a verbose description (bash/zsh
# builtins, `help command`); bash's `--help` prints usage and exits. These
# are read-only queries like `which`/`type` — `command -v kill` must stay
# allowed. Note `command -p kill …` (default-PATH execution) still runs the
# target and is NOT here.
COMMAND_QUERY_FLAGS = frozenset({"-v", "-V", "--help"})


def _is_command_query(args: list[str]) -> bool:
    """bash only recognizes `command`'s own options ahead of the command word
    (``command [-pVv] name [arg …]``): the scan stops at the first non-option
    word, so a flag seen later belongs to the TARGET — ``docker compose down
    -v`` passes -v to compose (--volumes), it is not a query flag (review
    #629 P2-1: scanning every position let that form through)."""
    options = takewhile(lambda arg: arg.startswith("-"), args)
    return any(arg in COMMAND_QUERY_FLAGS for arg in options)


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


def _shell_split_words(text: str) -> list[str]:
    """Split one segment into words the way a shell does, THEN quote-remove
    per word (POSIX order: field splitting happens before quote removal).
    Splitting on unquoted whitespace means ``echo 'safe; make prod-down'``
    yields ``["echo", "safe; make prod-down"]`` — the quoted payload is one
    inert word — while ``make 'prod-down'`` yields ``["make", "prod-down"]``
    so denylist matching still sees the plain name. Backslash outside quotes
    escapes the next character; inside double quotes it escapes only
    ``" \\ $ ` `` (POSIX); ``$'…'`` (ANSI-C) and ``$"…"`` (locale) drop the
    ``$`` and behave like their base quote kind; concatenated quoting
    (``pr"od-down"``) yields one word."""
    words: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            words.append("".join(current))
        current = []

    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "'":
            end = text.find("'", index + 1)
            if end == -1:  # unterminated: the rest is one literal word
                current.append(text[index + 1 :])
                index = length
            else:
                current.append(text[index + 1 : end])
                index = end + 1
        elif char == '"':
            index += 1
            while index < length:
                inner = text[index]
                if inner == '"':
                    index += 1
                    break
                if (
                    inner == "\\"
                    and index + 1 < length
                    and text[index + 1] in ('"', "\\", "$", "`")
                ):
                    current.append(text[index + 1])
                    index += 2
                else:
                    # `$` before `{`/`(`/name is variable expansion — an
                    # accepted gap — and `$'` inside "…" is literal data;
                    # either way the character itself is the word content.
                    current.append(inner)
                    index += 1
        elif char == "\\" and index + 1 < length:
            current.append(text[index + 1])
            index += 2
        elif char == "$" and index + 1 < length and text[index + 1] in "'\"":
            # `$'…'` (ANSI-C) / `$"…"` (locale) quoting: the `$` carries no
            # word content — drop it and let the quote branch handle the rest
            # (keeps `make $'prod-down'` matching, the #629 review case).
            index += 1
        elif char.isspace():
            flush()
            index += 1
        else:
            current.append(char)
            index += 1
    flush()
    return words


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
    wrapper (`nice -n 10 …` vs `sudo -n …` — the same flag differs).
    sudo's short list is complete per sudo 1.9 `sudo --help`/`man sudo`
    OPTIONS (verified on this machine, sudo 1.9.13p2): value-taking short
    options are -C (close-from), -D (chdir), -g (group), -h (host), -p
    (prompt), -R (chroot), -T (command-timeout), -U (other-user), -u (user);
    all other short flags (-A -B -b -E -e -i -K -k -l -N -n -P -S -s -V -v)
    take no value, and long --askpass is the boolean twin of -A — it must
    NOT consume the next word (review #629 P2-2: listing it made `sudo
    --askpass make prod-down` eat `make` and allow the command). A
    prompt/dir value is ordinary data, not the command —
    treating it as the command makes `sudo -p 'Password: ' make prod-down`
    see `Password:` as the command and MISS the real one (review #629).
    The long names are included for the separated form `sudo --prompt x …`
    (man SYNOPSIS `[-p prompt]`; also verified live: `sudo --host localhost
    -n true` consumes the host instead of erroring on it as an unknown
    command). `--opt=value` needs no entry: the value rides inline.
    Other wrappers list their value-taking short options; their long names
    (where getopt-style `--name value` exists, e.g. env's `--unset NAME`)
    follow the same convention."""
    mapping = {
        "sudo": (
            "-C",
            "-D",
            "-g",
            "-h",
            "-p",
            "-R",
            "-T",
            "-U",
            "-u",
            "--chdir",
            "--close-from",
            "--command-timeout",
            "--group",
            "--host",
            "--other-user",
            "--prompt",
            "--chroot",
            "--user",
        ),
        "nice": ("-n", "--adjustment"),
        "ionice": ("-n",),
        "env": ("-u", "-C", "-S", "--unset", "--chdir", "--split-string"),
        "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
        "xargs": ("-I", "-n", "-P", "-L", "-s", "-d"),
        "timeout": ("-k", "-s"),
    }
    return mapping.get(wrapper, ())


# sudo modes that run the command through the target user's shell instead of
# exec'ing it directly (sudo 1.9 `--help`: "-s, --shell run shell as the
# target user; a command may also be specified" — same for -i/--login; the
# command string is handed to $SHELL -c, which re-parses it as shell text).
SUDO_SHELL_FLAGS = frozenset({"-s", "-i", "--shell", "--login"})


def _sudo_shell_text(tail: list[str], after: list[str]) -> str | None:
    """The command string sudo's shell modes hand to ``$SHELL -c``, or None
    when no shell flag was peeled. Only the option region ``_skip_options``
    actually consumed counts — ``sudo kill -s 1`` passes -s to kill, it is
    not sudo's shell mode. That region is the prefix of ``tail`` missing
    from ``after`` (skipping only ever removes leading tokens). Without
    this, ``sudo -s 'make prod-down'`` reached the denylist as ONE argv
    word and passed (review #629 P2-3)."""
    return " ".join(after) if SUDO_SHELL_FLAGS & set(tail[: len(tail) - len(after)]) else None


def _identify(tokens: list[str]) -> tuple[str, list[str]] | None:
    """The real command behind leading assignments and wrappers: returns
    (basename, args) or None for an assignment-only segment. sudo's shell
    modes are handled inline: their remaining words are a $SHELL -c command
    string, recursed into here — the segment is then fully checked, so None
    is returned for the caller too."""
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
            tail = rest[1:]
            after = _skip_options(tail, _wrapper_value_opts(_basename(head)))
            if _basename(head) == "sudo" and (text := _sudo_shell_text(tail, after)):
                _check_shell_text(text)
                return None
            rest = after
        else:
            return _basename(head), rest[1:]
    return None


def _skip_options(tokens: list[str], value_opts: tuple[str, ...]) -> list[str]:
    remaining = tokens
    while remaining and remaining[0].startswith("-"):
        token = remaining[0]
        if token == "--":
            # end of options; everything after is the command
            return remaining[1:]
        if token.startswith("--"):
            # Long options: `--opt=value` carries its value inline; `--opt
            # value` (the SYNOPSIS form of `sudo --prompt x …`, verified to
            # be consumed — `sudo --host localhost -n true` never prints
            # help) consumes the following token when the name takes a value.
            name = token.split("=", 1)[0]
            takes_value = name in value_opts and "=" not in token
            remaining = remaining[1:]
            if takes_value and remaining:
                remaining = remaining[1:]
            continue
        takes_value = token in value_opts
        remaining = remaining[1:]
        if takes_value and remaining:
            remaining = remaining[1:]
    return remaining
