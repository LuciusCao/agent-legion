"""Service-lifecycle command guard for Studio ACP terminals (issue #629).

Incident: an agent ran ``make prod-down && make prod-up`` through its ACP
terminal; the session died between the two halves and production stayed down
until a human noticed. Principle: an agent may do anything *reversible* —
operations that cannot self-recover once interrupted belong to a human at a real terminal.

Layering with the permission chain: the agent-side Bash tool is gated by
``session/request_permission`` BEFORE ``terminal/create`` (terminals.py), so
commands reaching this guard were in principle already approved. This guard
is a platform-level hard line ON TOP of that approval chain: the approving
surface (the chat UI served by the very backend these commands take down)
dies with the session, so chat-side approval can never make them safe —
they are refused unconditionally and the operator uses a real terminal.
``scripts/prod-restart.sh`` is the atomic human entry for restarts
(down + up + health-check + failure retry), which the block message points
to. Same philosophy as velites' ``command_guard.rs`` (the footgun-guard
precedent): a heuristic over shell text, NOT a security boundary —
adversarial evasion via variables (``D=kill; $D 1``) or command
substitution (``make $(echo prod-down)``) is accepted because the threat
model is a well-meaning agent doing ops casually.

Matching is command-level, never substring-level: text is split into shell
segments (``;`` ``|`` ``&`` ``&&`` newlines/CR, parens and command
substitutions — quote-aware; the state machines live in the sister module
terminal_guard_words.py), each segment is split into words by shell
semantics and quote-removed per word, the real command is identified
behind shell keywords and wrappers (if/while/!/coproc, sudo/env/nice/
timeout/xargs/…), and only that command word (plus the operands of
``make``/``sh``/``source``/``docker``) is compared against the denylist.
Backtick bodies are re-lexed with backtick rules (``\\<newline>`` joins
inside ``'…'`` regions of the span — round-7 C1 — and a case pattern's
``)`` is span text, never a frame closer — round-8 C1'); ``$(…)`` bodies
keep single-quote regions literal (round-6 M2).
``echo "prod-down"`` and ``grep prod-down Makefile`` therefore stay allowed
— and because quote removal happens per word AFTER the split, quoted text
containing separators (``echo 'safe; make prod-down'``, a ``-c`` payload
that merely prints lifecycle names) stays data. Nested shells (``bash
-c '…'``, including short-option clusters like ``-lc``) and ``eval``
recurse into their command string with its inner quoting intact.

Known gaps (explicitly registered after the #707 adversarial review): the
runner family is open-ended and NOT fully enumerated — ``expect``,
``script``, ``parallel``, ``at``, ``sshpass``, interpreters running code
from ``-c``/``-e`` strings (``python3 -c "os.system(…)"``) and stdin-fed
or process-substitution-fed shells (``bash <(echo …)``, ``curl … | sh``)
all pass; only the high-frequency wrappers (``watch``, ``find -exec``) are
recursed into. So do: unresolved variables (``! $CMD``, ``D=kill; $D 1``),
brace expansion, heredoc bodies, backslash-newline continuations,
``exec``/``su``/``ssh`` prefixes, leading redirections (``2>&1 cmd``),
``env -S 'VAR=x cmd …'`` (the string is re-parsed by env itself, not
recursed into), ``make -f -`` reading a makefile from stdin, and bash
builtins (``builtin kill``, ``enable -f``) — while ``builtin export/
declare/typeset`` and ``declare``/``typeset -x`` DO reach the env-injection
gate (round-3 M1). ``$'…'`` ANSI-C escapes are
in the same family (round-6 I5): bash/zsh/sh/ksh evaluate ``$'\x70kill'``
into a real pkill while the guard keeps the backslash literal — the gap is
closed only for plainly-spelled names (pure ASCII here), never for
escape-evaluated ones. Malformed text — an unterminated quote or command
substitution — is refused outright: escape-ambiguous substitution bodies
execute differently across shells (ksh vs POSIX), so no single parse can
be right (round-3 H1).

Env-injection keys (BASH_ENV/ENV/ZDOTDIR/PROMPT_COMMAND/SHELLOPTS/
BASHOPTS, relative-segment PATH) are refused on every channel that can
reach this guard — env overrides, assignment prefixes, ``env`` arguments
and the export spellings — one table, one risk (a startup script sourced
before/around the checked command).
"""

from __future__ import annotations

from itertools import takewhile
from posixpath import normpath

from server.app.studio_chat.terminal_guard_words import (
    UnclosedShellConstruct,
    _segments,
    _shell_split_words,
)

__all__ = [
    "BLOCKED_COMMANDS",
    "BLOCKED_COMPOSE_SUBCOMMANDS",
    "BLOCKED_MAKE_TARGETS",
    "BLOCKED_SCRIPTS",
    "TerminalCommandBlockedError",
    "ensure_terminal_command_allowed",
]

# make targets that start/stop the platform's own services. Both halves of
# the pair are listed: interrupting `prod-down` leaves services down with
# nothing to bring them back (the session that ran it is gone — #629), and a
# half-finished `prod-up` is the same outage. The dev stack targets follow:
# the dev backend hosts the Studio session itself, so killing it kills the
# approving channel mid-flight. `stack-*` targets cover the Docker form.
BLOCKED_MAKE_TARGETS = frozenset(
    "prod-up prod-down prod-restart stack-host-up stack-host-down "  # noqa: SIM905 — words-per-line keeps the budget
    "stack-worker-up stack-worker-down stack-down dev-up dev-down".split()
)

# Direct script invocations of the same lifecycle entry points, matched by
# basename so any worktree/path prefix (`./scripts/…`,
# `/path/to/.worktrees/prod/scripts/…`) is covered.
BLOCKED_SCRIPTS = frozenset(
    "native-prod-up.sh native-prod-down.sh prod-restart.sh stack-prod-up.sh dev_stack.sh".split()  # noqa: SIM905 — words-per-line keeps the budget
)

# Process- and machine-level commands, refused unconditionally in the Studio
# terminal context. `kill`/`pkill`/`killall` have no legitimate target here:
# the agent's own child processes are managed by the terminal protocol itself
# (terminal/kill takes down the whole process group), so any raw kill aims at
# someone else's process — including the backend hosting this session.
# launchctl/systemctl (load/unload/bootout) and shutdown/reboot/halt/poweroff
# are service/host lifecycle by definition.
BLOCKED_COMMANDS = frozenset(
    "kill pkill killall launchctl systemctl shutdown reboot halt poweroff".split()  # noqa: SIM905 — words-per-line keeps the budget
)

# Compose/launchd-family subcommands whose interruption leaves services off
# (down/stop/restart/kill take the stack down; `up` alone is rerunnable and
# stays allowed). `docker compose down` is the Docker prod-down; `brew
# services stop/restart/unload/kill` wraps launchctl on macOS. `rm` sits in
# both this set and BLOCKED_DOCKER_SUBCOMMANDS (compose rm removes service
# containers).
BLOCKED_COMPOSE_SUBCOMMANDS = frozenset({"down", "stop", "restart", "kill", "rm"})
BLOCKED_BREW_SERVICES_SUBCOMMANDS = frozenset({"stop", "restart", "unload", "kill"})
# First-level `docker` subcommands that stop/remove a container directly
# (bypassing the compose form; stack-prod-up containers are stoppable one by
# one via `docker stop`, which equals `compose stop` in effect — attack
# report #707 HIGH-8). `run` is checked separately (host-root bind mounts).
BLOCKED_DOCKER_SUBCOMMANDS = frozenset({"stop", "kill", "restart", "rm"})

# Leading wrapper tokens skipped when identifying the real command; their
# option flags (and value-taking flags) are skipped along the way. `command`
# is NOT here: it is a shell builtin with read-only query modes (-v/-V),
# handled in _check_command instead of peeled blindly. The util-linux
# scheduling wrappers beyond nice (ionice, round-7 M1) ride the same peel;
# taskset/chrt/setarch/flock/nsenter/unshare/runuser stay in the open-ended
# gap below (positional-mask/pid/lock-file operands their first non-option
# word is NOT the command, or root-only/namespace-attack-only reach).
WRAPPERS = frozenset("sudo env time nice nohup setsid stdbuf xargs ionice".split())  # noqa: SIM905
# ionice's act-on-running-process MODE flags: once one is peeled — in ANY
# spelling, glued ``-p123``, split ``-p 123`` or inline ``--pid=123`` — the
# real tool (util-linux 2.38, live) treats every remaining word as a PID/
# PGID/UID argument and errors out without running any command, so no
# command position remains and _identify drops the rest (round-8 M3': the
# glued form used to peel ``-p123`` as a boolean and land pkill in the
# command slot — the split form's mirror image, both must not block).
# ``--t`` is deliberately absent everywhere: it is ``--ignore``'s unique
# GNU prefix and BOOLEAN — adding it (or --ignore/--i/--ig) to the value
# table would make ``ionice --t kill`` eat ``kill`` as the value and MISS a
# real execution; the current BLOCK on it is exactly right.
_IONICE_PID_MODE = ("-p", "-P", "-u", "--pid", "--pgid", "--uid")
# Shells whose `-c <string>` argument is a full shell command text (recursed
# into); without `-c` their file operands are script invocations (basename
# check). Combined short clusters (`-xc`, `-lc`) count as `-c`.
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})

# Shell keywords that can hold the command position without being commands
# (POSIX reserved words + `!` + the two flow-control blocks `{`/`}`). After
# segmentation `if make prod-down` arrives as one segment whose command word
# is `if` — the real command sits in the SAME segment, so identification
# skips these tokens and re-reads the next word (attack report #707
# CRITICAL-2: `if make prod-down; then :; fi` and the agent-favorite
# `while ! make prod-up; do sleep 2; done` used to pass wholesale). The
# words themselves are not denylist candidates: `[` IS a real command
# (`/usr/bin/[`) and `then`/`do`/`fi`/`done` never head a lifecycle command.
# `coproc` is bash/zsh-only but harmless to skip under sh too.
SHELL_KEYWORDS = frozenset(
    "if then else elif fi while until do done for in case esac ! coproc { }".split()  # noqa: SIM905 — words-per-line keeps the budget
)

# Runners whose remaining argv is itself a shell command line: the payload
# is joined and recursed into like a `sh -c` string. `watch` runs its argv
# via `sh -c` every interval (procps: "Commands are executed... via execvp
# if possible, otherwise via `sh -c command`"); `find`'s ``-exec … \;``/
# `-execdir … +` template is a command template by construction — these are
# the two high-frequency evasions worth structural handling (attack report
# #707 HIGH-6); the long tail (expect/script/parallel/at/…) stays a
# registered gap instead of an endless game of whack-a-mole.
_RECURSING_RUNNERS = frozenset({"watch", "find"})

# Env names refused on every channel (table below). BASH_ENV is sourced by
# every non-interactive bash, ENV by an interactive sh/dash, ZDOTDIR
# relocates zsh's startup directory (.zshenv sourced before any command —
# round-3 M2, verified live), PROMPT_COMMAND/SHELLOPTS/BASHOPTS steer the
# shell itself; PATH is handled separately (_path_override_allowed). The
# same names arrive as command-line assignment prefixes, `env`/`sudo`
# NAME=value arguments and export/declare -x/typeset -x spellings (review
# #707 R1, round-3 M1) — one table, one risk: a startup script sourced
# before/around the checked command.
BLOCKED_ENV_NAMES = frozenset(
    "BASH_ENV ENV ZDOTDIR PROMPT_COMMAND SHELLOPTS BASHOPTS".split()  # noqa: SIM905 — words-per-line keeps the budget
)


def _assignment_env_name(token: str) -> str | None:
    """The NAME half of a ``NAME=value`` word, or None when the word is not
    an assignment-shaped env prefix (an invalid name like ``A-B=1`` is a
    command word, exactly how real shells treat it). Exec-form argv arrives
    pre-split, so a leading ``BASH_ENV=x.sh`` word is the env-var prefix of
    the shell grammar (and `env` reuses the same shape for its arguments)."""
    name, eq, value = token.partition("=")
    if not eq or not value or not (name[:1].isalpha() or name[:1] == "_"):
        return None
    return name if all(char.isalnum() or char == "_" for char in name) else None


def _refuse_injected_env(name: str, value: str) -> None:
    """One env-pair decision shared by the process channel (_check_env) and
    the command-line channels (assignment prefixes, `env` arguments)."""
    if name in BLOCKED_ENV_NAMES:
        raise _blocked(f"环境变量注入 {name}")
    if name == "PATH" and not _path_override_allowed(value):
        raise _blocked("PATH 覆盖含空段（. 或相对路径），可能劫持命令解析")


# Minimum nesting depth the guard accepts; beyond it the text is refused
# instead of recursing to a RecursionError (fail-closed either way — the SDK
# turns handler exceptions into JSON-RPC errors — but a clean block beats a
# 331-frame traceback in the logs; attack report #707 MEDIUM-10).
_MAX_RECURSION_DEPTH = 64


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


def ensure_terminal_command_allowed(
    command: str,
    args: list[str] | None,
    env: list[tuple[str, str]] | None = None,
) -> None:
    """Check the exec-form request (command + argv + env) before spawn; raises on deny.

    env arrives as (name, value) pairs from the ACP EnvVariable list: the
    process environment is a second, unchecked command channel — BASH_ENV
    names a startup script a non-interactive bash sources BEFORE the checked
    command runs, so a clean `bash -c "echo hi"` plus a poisoned env entry
    executed attacker script wholesale (attack report #707 HIGH-4). None
    (the inherit case) skips the env pass; values are never inspected for
    command text — only the injection keys are refused, so ordinary
    LANG/LC_ALL/… overrides keep passing untouched.
    """
    if env:
        for name, value in env:
            _refuse_injected_env(name, value)
    _check_words([command, *(args or [])])


def _path_override_allowed(value: str) -> bool:
    """A PATH override may only swap in absolute directories. Legitimate
    customization (adding a toolchain dir, prepending ~/.local/bin) is all
    absolute paths; an empty segment or ``.`` makes the CWD — attacker- or
    agent-controlled — shadow system commands like make/bash with an
    impostor of the same name, which the command-text guard cannot see.
    Wholesale rejection would misfire on the legitimate absolute form, and
    an absolute-only PATH pointing at an attacker directory is equivalent to
    already having file-write powers (out of scope for a text guard)."""
    parts = value.split(":")
    return all(part.startswith("/") for part in parts)


def _check_shell_text(text: str, _depth: int = 0) -> None:
    if _depth >= _MAX_RECURSION_DEPTH:
        raise _blocked("嵌套 shell 层数过深")
    try:
        # Malformed input (unterminated quote/substitution) is refused by
        # _segments itself — round-3 H1 rationale in its docstring.
        segments = _segments(text)
    except UnclosedShellConstruct as reason:
        raise _blocked(f"未闭合的引号或命令替换（{reason}）") from reason
    for segment in segments:
        words = _shell_split_words(segment)
        if words:
            _check_words(words, _depth)


def _check_words(words: list[str], _depth: int = 0) -> None:
    # _shell_split_words (or exec-form argv, which arrives pre-split and
    # pre-unquoted by the ACP client) is the ONLY place quote removal may
    # happen: unquoting must follow word splitting, never precede it, so a
    # quoted word containing separators (``echo 'safe; make prod-down'``)
    # stays one inert word instead of being re-parsed as commands.
    identified = _identify(words, _depth)
    if identified is not None:
        _check_command(identified[0], identified[1], _depth)


def _check_command(cmd: str, args: list[str], _depth: int = 0) -> None:
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
        _, rest = _skip_options(args, ())
        if rest:
            _check_command(_basename(rest[0]), rest[1:], _depth)
        return
    if cmd in ("export", "declare", "typeset"):
        # `export BASH_ENV=x; bash -c …` is the two-segment twin of the
        # assignment-prefix injection (review #707 R1): the exported name
        # outlives the segment and reaches every later shell. declare/typeset
        # are export's spellings behind attribute flags — only ``-x`` makes
        # the assignment an export (round-3 M1: `declare -x BASH_ENV=…; bash
        # -c …` really sourced the script, verified live; plain `declare
        # NAME=…` / `+x` stay inert). `set -a` stays in the registered
        # variable-gap family; a bare `export PATH` (no value) is a no-op.
        _check_export_arguments(cmd, args)
        return
    if cmd == "builtin" and args and args[0] in ("export", "declare", "typeset"):
        # Same gates behind the `builtin` prefix (round-3 M1); `builtin kill
        # …` keeps its raw-command-word denylist matching (registered gap).
        _check_export_arguments(args[0], args[1:])
        return
    hit = next((arg for arg in args if _make_target(arg) in BLOCKED_MAKE_TARGETS), None)
    if cmd == "make" and hit is not None:
        raise _blocked(f"make {_make_target(hit)}")
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
            _check_shell_text(" ".join(args[position + 1 :]), _depth + 1)
        else:
            _check_script_operands(cmd, args)
        return
    if cmd in ("source", "."):
        _check_script_operands(cmd, args)
        return
    if cmd == "eval":
        _check_shell_text(" ".join(args), _depth + 1)
        return
    if cmd in _RECURSING_RUNNERS:
        # `watch make prod-down` / `find … -exec make prod-down \;`: the
        # runner's trailing argv is a command line (watch: as a whole; find:
        # the -exec template is what precedes the terminator `;`/`+`).
        _check_runner_args(cmd, args, _depth)
        return
    if cmd == "docker":
        _check_docker(args, _depth)
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


def _check_export_arguments(cmd: str, args: list[str]) -> None:
    """The env-assignment gate for `export` and its declare/typeset
    spellings: every NAME=value word goes through _refuse_injected_env.
    For declare/typeset only an ``x`` attribute seen in the flag prefix
    makes the assignment an export (``declare -gx BASH_ENV=…`` injects,
    verified live; plain ``declare NAME=…`` sets an unexported shell
    variable and ``+x`` REMOVES the attribute — both verified inert).
    bash's attribute letters: ``declare [-aAfFgiIlnrtux]``."""
    exported = cmd not in ("declare", "typeset")
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-") and "=" not in arg and (arg[1:2].isalpha() or arg[1:2] == "-"):
            letters = arg.lstrip("-+")
            if letters and all(char in "aAfFgiIlnrtux" for char in letters):
                exported = exported or ("x" in letters and arg[1] != "+")
            continue
        if exported and (name := _assignment_env_name(arg)) is not None:
            _refuse_injected_env(name, arg.partition("=")[2])


def _make_target(arg: str) -> str:
    """make sees `name=value` words as command-line variable overrides, not
    targets — strip the assignment to recover the word make would act on...
    except that GNU make then runs the DEFAULT goal, not the stripped word:
    `make prod-down=1` sets a variable and runs the first/default target,
    which in a Makefile whose default goal is a lifecycle target IS the
    blocked command (verified: with `.DEFAULT_GOAL := prod-down` the real
    make runs it). Matching the stripped word closes the direct-name bypass
    at zero false-positive cost — no legit target carries `=` (make treats
    such words as assignments, never targets)."""
    return arg.split("=", 1)[0]


# watch's value-taking option is -n/--interval ONLY (procps 3.x and 4.x
# `man watch`, live-verified on 4.0.2/4.0.4: -g/--chgexit, -p/--precise,
# -t/--no-title, -e/--errexit, -c/--color, -d/--differences (optional
# argument, inline-only), -x/--exec, -b/--beep and -l are all flags). The
# round-1..5 table wrongly listed those flags as value-taking, so
# `watch -g make prod-down` fed make to the flag and passed while the
# real watch ran it every interval (round-6 live re-test); the tuple is
# inlined at its single use site (_check_runner_args) below.


def _check_runner_args(cmd: str, args: list[str], _depth: int) -> None:
    if cmd == "watch":
        _, watched = _skip_options(args, ("-n", "--interval"))
        _check_shell_text(" ".join(watched), _depth + 1)
        return
    # find: the -exec/-execdir template is the words after the flag up to
    # the terminator `;` / `+` (or end of args — an unterminated template is
    # a find syntax error; checking it anyway only errs on the strict side).
    for index, arg in enumerate(args):
        if arg in ("-exec", "-execdir", "-ok", "-okdir"):
            template = list(takewhile(lambda word: word not in (";", "+"), args[index + 1 :]))
            _check_shell_text(" ".join(template), _depth + 1)


def _host_root_mount(spec: str) -> bool:
    """Whether a docker bind-mount source spells the host root. Docker (and
    the kernel bind mount under it) normalizes the source BEFORE mounting,
    so ``//``, ``/./``, ``/../`` and friends all mount ``/`` while a plain
    string compare sees something else (round-3 H2: those really mounted
    the host root on docker 29.7.2; posixpath.normpath keeps a leading
    ``//``, so the leading slash run is folded first). /proc/self/root and
    /proc/1/root ARE the host root by definition (the container's own /
    init's root is the host root on Linux deployments)."""
    if spec in ("/proc/self/root", "/proc/1/root"):
        return True
    return normpath("/" + spec.lstrip("/")) == "/"


def _volume_source(spec: str) -> str:
    """The host path of a docker bind-mount arg — the `source` half of
    `source:dest[:opts]` with any flag prefix (``-v``, ``--volume=``)
    stripped first."""
    for prefix in ("--volume=", "-v"):
        if spec.startswith(prefix):
            return spec[len(prefix) :].split(":", 1)[0]
    return spec.split(":", 1)[0]


def _mount_source(spec: str) -> str | None:
    """The host path of a docker ``--mount`` comma-separated key=val body,
    or None when it is not a bind mount: the host-root form is
    ``type=bind`` with ``src``/``source`` spelling the root; `dst`-only
    mounts default to the same path INSIDE the container, and
    `type=tmpfs`/`volume` never touch the host root."""
    if not spec or "=" not in spec:
        return None
    fields = dict(
        part.partition("=")[::2]
        for part in spec.split(",")
        if "=" in part  # type: ignore[misc]
    )
    if fields.get("type") != "bind":
        return None
    return fields.get("src", fields.get("source"))


def _check_docker(args: list[str], _depth: int) -> None:
    """First-level docker CLI handling. Compose form: the `compose`
    subcommand's own denylist (down/stop/restart/kill/rm). Direct container
    operations: `docker stop/kill/restart/rm` equal `compose stop` in effect
    for a stack-prod deployment. `docker exec` runs a command inside a
    container — the argv after the container name is checked as a shell text
    (options first; the first non-option word is the container name, the
    rest the command line: `docker exec c make prod-down` recurses into
    `make prod-down`). `docker run`: full image/args parsing is ambiguous
    (volume specs, image names, entrypoint overrides), so only the
    host-root bind mount — the direct path to running anything on the host
    via chroot — is refused; other run forms are a registered gap."""
    if "compose" in args:
        if any(arg in BLOCKED_COMPOSE_SUBCOMMANDS for arg in args):
            raise _blocked("docker compose …")
        return
    if args and args[0] in BLOCKED_DOCKER_SUBCOMMANDS:
        raise _blocked(f"docker {args[0]} …")
    if args and args[0] == "exec":
        _, rest = _skip_options(args[1:], ("-u", "--user", "-w", "--workdir", "-e", "--env"))
        if rest:
            _check_shell_text(" ".join(rest[1:]), _depth + 1)
        return
    if args and args[0] == "run":
        # A bind-mount spec is `source:dest[:opts]`; the host-root mount is
        # the direct path to running anything on the host (chroot into it),
        # so only THAT is refused — an ordinary data-volume mount
        # (`-v /data:/data`) is fine. Covers inline (`-v /:/host`),
        # `--volume=/…` and the separated `-v <spec>` form alike (the bare
        # flag token itself is NOT a spec — double-counting it made the
        # empty source normalize to `/` and misfire on `-v //data:/data`).
        # The `--mount key=val` spelling (R5; H2 adds the normalized-root
        # and /proc/*/root spellings) is parsed separately — comma fields.
        specs = [a for a in args if a.startswith(("-v", "--volume=")) and a != "-v"]
        specs += [args[i + 1] for i, a in enumerate(args) if a == "-v" and i + 1 < len(args)]
        specs += [args[i + 1] for i, a in enumerate(args) if a == "--volume" and i + 1 < len(args)]
        mounts = [
            a[len("--mount=") :] if a.startswith("--mount=") else args[i + 1]
            for i, a in enumerate(args)
            if a.startswith("--mount") and (a.startswith("--mount=") or i + 1 < len(args))
        ]
        if any(_host_root_mount(_volume_source(s)) for s in specs) or any(
            src is not None and _host_root_mount(src) for src in map(_mount_source, mounts)
        ):
            raise _blocked("docker run 挂载宿主根目录")


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


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _is_shell_c_flag(token: str) -> bool:
    return token.startswith("-") and not token.startswith("--") and "c" in token[1:]


# A timeout/`<n>unit` duration: digits with an optional single-letter suffix
# (s m h d), optional fractional part, or `infinity`. GNU coreutils also
# accepts composites — `1m30s`, a repeatable digits+unit sequence (info
# coreutils "timeout invocation": "UNIT may be... Multiple units may be
# given in decreasing order" — `1m30s` = 90s), and on versions that support
# them the composite IS executed: refusing the form makes `1m30s` the
# command word and swallows the real command into its arguments (review
# #707 M1: `timeout 1m30s make prod-down` passed with make as a duration
# argument). Accepting composites keeps the real command in command
# position; a wrongly-accepted duration word only means treating the next
# word as the command, which the denylist then checks.
def _is_duration(token: str) -> bool:
    if token == "infinity":
        return True
    index = 0
    length = len(token)
    if not length:
        return False
    unit_seen = False
    while index < length:
        start = index
        while index < length and (token[index].isdigit() or token[index] == "."):
            index += 1
        number = token[start:index]
        if not number or number.count(".") > 1:
            return False
        if index < length and token[index] in "smhd":
            index += 1
            unit_seen = True
        elif not unit_seen:
            # A bare number is a valid duration; a later bare number after
            # a unit (`1m30`) is NOT — composite parts must carry units.
            return index == length
    return True


def _wrapper_value_opts(wrapper: str) -> tuple[str, ...]:
    """Option flags that consume the following token as their value, keyed by
    wrapper (`nice -n 10 …` vs `sudo -n …` — the same flag differs). sudo's
    list is per sudo 1.9 `man sudo` OPTIONS (verified live, 1.9.13p2), incl.
    the SELinux context pair `-r role`/`-t type` (P1-1: their long twins
    --role/--type ride the same entries; a cluster `-rX…`/`-tX…` treats the
    rest of the cluster as the role/type name, per the -C live check);
    boolean twins must NOT consume the next word (review #629 P2-2:
    listing --askpass made `sudo --askpass make prod-down` eat `make`), and
    a prompt/dir value is data, not the command — treating it as the
    command makes `sudo -p 'Password: ' make prod-down` see `Password:` as
    the command and MISS the real one. `--opt=value` rides inline and needs
    no entry; other wrappers follow the same short+long convention."""
    mapping: dict[str, tuple[str, ...]] = {
        "sudo": tuple(
            "-C -D -g -h -p -R -r -T -t -U -u".split()  # noqa: SIM905 — words-per-line keeps the budget
            + "--chdir --close-from --command-timeout --group --host --other-user --prompt --chroot --role --type --user".split()  # noqa: SIM905
        ),
        "nice": ("-n", "--adjustment"),
        # ionice (util-linux 2.38, live: -c/--class and -n/--classdata take
        # values; -t/--ignore is boolean; -p/-P/-u are MODE flags handled
        # in _identify via _IONICE_PID_MODE — never value entries here).
        "ionice": ("-c", "-n", "--class", "--classdata"),
        "env": ("-u", "-C", "-S", "--unset", "--chdir", "--split-string"),
        "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
        # xargs (GNU findutils 4.9 + BSD macos): -a/--arg-file reads the
        # utility's INPUT from a file but the utility still runs (`xargs -a
        # /tmp/pids kill` really executes kill — P1-2); BSD-only -J/-R/-S
        # join GNU's -I/-L/-n/-P/-s/-E/-d, and --process-slot-var is
        # REQUIRED-argument like --max-args (GNU getopt; round-5 C3: bare
        # `xargs --process-slot-var v kill` eats v as the var name and
        # really runs kill — earlier it was misfiled with the optional
        # family; BSD has no such option). NOT value-taking: -o/--open-tty
        # and -i/-l/-e are boolean-or-INLINE-optional (bare `-i kill`
        # really runs kill), and --replace/--eof/--max-lines are excluded
        # with them (GNU getopt optional-argument live check).
        "xargs": tuple(
            "-I -J -L -n -P -s -d -E -R -S -a".split()  # noqa: SIM905 — words-per-line keeps the budget
            + "--arg-file --max-args --max-procs --max-chars --delimiter --process-slot-var".split()  # noqa: SIM905
        ),
        "timeout": ("-k", "-s", "--kill-after", "--signal"),
        # /usr/bin/time (GNU time 1.9): -f/--format and -o/--output take
        # values; -v/-p are boolean report modes (live-verified, round-6).
        # BSD time is the mirror-image family: -f/-v unrecognized (usage
        # exit), -o/-l boolean (macOS-verified) — never value-taking, so
        # this table errs strict on BSD only.
        "time": ("-f", "-o", "--format", "--output"),
    }
    return mapping.get(wrapper, ())


# sudo modes that run the command through the target user's shell instead of
# exec'ing it directly (sudo 1.9 `--help`: "-s, --shell run shell as the
# target user; a command may also be specified" — same for -i/--login; the
# command string is handed to $SHELL -c, which re-parses it as shell text).
SUDO_SHELL_FLAGS = frozenset({"-s", "-i", "--shell", "--login"})
# Their short-option letters, for CLUSTERED flags: `sudo -Es cmd` is -E plus
# -s (sudo 1.9 getopt concatenation; `sudo -Es 'make prod-down'` really runs
# the string through $SHELL -c — attack report #707 CRITICAL-3). Mirrors how
# _is_shell_c_flag treats `bash -xc`.
SUDO_SHELL_FLAG_LETTERS = frozenset({"s", "i"})


def _cluster_flag_letters(token: str, value_opts: tuple[str, ...]) -> str:
    """The flag letters of a clustered short option, stopping at the first
    value-taking letter — the rest of that cluster is that option's inline
    value (``-Csu`` is -C with value ``su``, NOT -C + -s + -u)."""
    letters = token[1:]
    for index, letter in enumerate(letters):
        if f"-{letter}" in value_opts:
            return letters[:index]
    return letters


def _sudo_shell_text(options: list[str], after: list[str]) -> str | None:
    """The command string sudo's shell modes hand to ``$SHELL -c``, or None
    when no shell flag was peeled. Only the option tokens ``_skip_options``
    consumed count — ``sudo kill -s 1`` passes -s to kill (never in the
    option region), and a value word eaten by a non-shell letter (`sudo -p
    -Es make …`: -Es is the PROMPT, not flags) is not an option at all,
    which is why the scan receives option tokens only. Without the cluster
    walk, ``sudo -su root 'make prod-down'`` reached the denylist with
    `root` as the command word and passed (review #707 R2: `-su` is -s plus
    -u taking the NEXT word — `sudo -su` alone reports "option requires an
    argument -- u")."""
    value_opts = _wrapper_value_opts("sudo")
    for token in options:
        if token in SUDO_SHELL_FLAGS:
            return " ".join(after)
        letters = _cluster_flag_letters(token, value_opts)
        if any(letter in SUDO_SHELL_FLAG_LETTERS for letter in letters):
            return " ".join(after)
    return None


def _identify(tokens: list[str], _depth: int = 0) -> tuple[str, list[str]] | None:
    """The real command behind leading assignments, shell keywords and
    wrappers: returns (basename, args) or None for an assignment-only
    segment. Shell keywords are skipped token by token until the first word
    that can be a command. An assignment in command position exports
    NAME=value for the command that follows — the name goes through the
    SAME injection gate as terminal/create's env channel (review #707 R1):
    ``BASH_ENV=x.sh bash -c …`` really sources x.sh first (verified live),
    and `env NAME=value cmd` / ``sudo NAME=value cmd`` arguments reach this
    same branch after their wrapper peel. sudo's shell modes are handled
    inline: their remaining words are a $SHELL -c command string, recursed
    into here — the segment is then fully checked, so None is returned for
    the caller too."""
    rest = tokens
    while rest:
        head = rest[0]
        if head in SHELL_KEYWORDS or _basename(head) in SHELL_KEYWORDS:
            rest = rest[1:]
        elif (env_name := _assignment_env_name(head)) is not None:
            _refuse_injected_env(env_name, head.partition("=")[2])
            rest = rest[1:]
        elif _basename(head) == "timeout":
            _, rest = _skip_options(rest[1:], _wrapper_value_opts("timeout"))
            # The duration positional: a duration-shaped word only. A
            # boolean long option (--preserve-status) no longer shifts
            # the real command into the duration slot — a non-duration
            # word is treated as the command and checked.
            if rest and _is_duration(rest[0]):
                rest = rest[1:]
        elif _basename(head) == "sudo":
            tail = rest[1:]
            options, after = _skip_options(tail, _wrapper_value_opts("sudo"))
            if text := _sudo_shell_text(options, after):
                _check_shell_text(text, _depth + 1)
                return None
            if after and any(char.isspace() for char in after[0]):
                # A command word containing whitespace cannot be execvp'd —
                # the quoted command-string spelling (`sudo -S 'make
                # prod-down'`, review #707 R4; real sudo command-not-founds
                # it, fail-safe re-reads the joined words as shell text).
                _check_shell_text(" ".join(after), _depth + 1)
                return None
            rest = after
        elif _basename(head) in WRAPPERS:
            base = _basename(head)
            opts, rest = _skip_options(rest[1:], _wrapper_value_opts(base))
            if base == "ionice" and any(opt.startswith(_IONICE_PID_MODE) for opt in opts):
                rest = []
        else:
            return _basename(head), rest[1:]
    return None


def _long_opt_takes_value(name: str, value_opts: tuple[str, ...]) -> bool:
    """Whether a long-option NAME consumes a value: an exact table entry, or
    (round-6 H1) its unique unambiguous ABBREVIATION — GNU getopt accepts
    any unambiguous prefix of a long option (`xargs --p v kill` really
    runs kill — 4.9.0 live matrix: 90 of 96 prefixes eat the value, the
    6 ambiguous ones error out), while the previous exact-token-only match
    let every one of them through. Ambiguous prefixes error in getopt
    (nothing runs), so a prefix matching MORE than one entry is treated as
    value-less — the conservative direction; single-letter prefixes are
    matched too (they are GNU-correct against these tables: the one live
    collision family, sudo's --p/--h, is ambiguous in sudo's FULL option
    set, so real sudo errors out where the guard over-blocks — never the
    bypass direction)."""
    long_names = [opt[2:] for opt in value_opts if opt.startswith("--")]
    return name in long_names or len([n for n in long_names if n and n.startswith(name)]) == 1


def _skip_options(tokens: list[str], value_opts: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Skip leading option tokens; returns (option tokens consumed, the
    remaining words — options and their value words are gone). Long options
    match by exact name or unique abbreviation, GNU getopt semantics
    (round-6 H1; the why on _long_opt_takes_value): `--opt=value` carries
    its value inline, `--opt value` (or a unique prefix like `--pre value`)
    consumes the following token when the name takes one. Short options
    are scanned LETTER BY LETTER (review #707 R2, cluster semantics on
    _cluster_flag_letters): on a value-taking letter the rest of the
    cluster is its inline value (drop the token) unless the letter is
    last, when the NEXT word is the value (drop it too). Whole-token
    matching instead let `sudo -su root 'make prod-down'` drop `root`
    into the command position and pass. Option tokens are returned
    separately from value words so callers can tell a flag from a value."""
    remaining = tokens
    options: list[str] = []
    while remaining and remaining[0].startswith("-"):
        token = remaining[0]
        if token == "--":
            # end of options; everything after is the command
            return options, remaining[1:]
        if token.startswith("--"):
            name = token.split("=", 1)[0][2:]
            takes_value = "=" not in token and _long_opt_takes_value(name, value_opts)
            options.append(token)
            remaining = remaining[1:]
            if takes_value and remaining:
                remaining = remaining[1:]
            continue
        value_next = False
        for index, letter in enumerate(token[1:]):
            if f"-{letter}" in value_opts:
                value_next = index == len(token) - 2
                break
        options.append(token)
        remaining = remaining[1:]
        if value_next and remaining:
            remaining = remaining[1:]
    return options, remaining
