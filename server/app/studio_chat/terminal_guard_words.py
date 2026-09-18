"""Shell text segmentation and word splitting for the terminal guard.

Sister module of terminal_guard.py (the #563 split convention): the guard's
matching pipeline is (1) split shell text into simple-command segments, (2)
split each segment into words with quote removal, (3) identify the real
command behind keywords/wrappers and compare against the denylist. Steps
(1) and (2) are quote-aware state machines kept here — pure parsing with no
denylist knowledge — while terminal_guard.py owns the denylist and the
command identification policy. Splitting lets each half stay within its
architecture budget without separating the denylist from ITS matching
semantics (only the shared parser vocabulary moved).

The rules these machines implement (verified against real sh/bash/zsh):
segment separators are ``;`` ``|`` ``&`` (covers ``&&``), newlines, CR
(interactive readline treats it as end-of-line; the word splitter's
``str.isspace`` already treats it as whitespace — the two must agree),
subshell parens and command substitutions (``$(…)`` and backticks — both
execute inside double quotes too, so they open segments there as well;
single quotes suppress everything). A command substitution opened INSIDE a
double-quoted string must restore the double-quote state when it closes:
both ``)`` and the closing backtick pop the same stack, whose frames carry
(restore_double, body_is_backtick) — the kind is what makes ``\\<newline>``
a continuation inside ``'…'`` regions of a backtick SPAN (round-7 C1). A
backtick closing that does not pop leaks the stale True, and the closing
quote of the host string is then misread as OPENING one — everything after
it, including a whole ``; make prod-down``, was swallowed into the quoted
word (attack report #707 CRITICAL-1).

A backslash outside single quotes pairs with the next character, and the
pair travels into the segment text RAW (round-4 P2 for the unquoted
context, round-5 for the rest — the three per-context branches were
converged into one): ``\\;`` ``\\|`` ``\\&`` are data, not separators
(`echo safe\\; make prod-down` is one echo; the splitter's identical
pairing re-merges the words), an escaped ``)``/backtick/``"`` cannot close
its construct, and ``\\<newline>`` is a line continuation — BOTH characters
dropped — in every context except single-quote regions OUTSIDE a backtick
span: unquoted, double-quoted, ``$"…"`` locale quotes (which arrive here
as plain double quotes) and $()/backtick bodies alike (round-5 C1/C2:
`make "prod-\\<NL>down"` ran in all five shells while the guard matched a
word containing a literal backslash and passed). Single-quote regions are
literal at the top level, inside ``"…"`` and inside ``$(…)`` bodies: a
substitution never opens inside a single-quote region, so the ``'`` seen
inside a body is a fresh quoting context there (round-6 M2: all five
shells keep `echo "$(make 'prod-\\<NL>down')"` literal). A BACKTICK body
is the exception (round-7 C1): its text is re-lexed with backtick rules,
where ``\\<newline>`` is a continuation EVERYWHERE in the span — ``'…'``
regions and nested ``$(…)`` bodies included — so all five shells really
ran ``echo `make 'prod-\\<NL>down'` `` and the join survives a nested
body (`` `x $(make 'prod-\\<NL>down')` `` runs prod-down too); the
parser drops the pair there as well, gated on ANY open backtick frame in
the body stack. Every other backslash stays literal in ``'…'`` (and
``$'…'`` ANSI-C quoting keeps ``\\<newline>`` literal too for the
SPLITTER's purposes — see the known-gap note in _shell_split_words's
``$'`` branch — the escapes it evaluates are a later gap, not a
segmentation concern). The one divergence is ksh's ``\\'`` inside a
body — it escapes the quote there while the other four shells keep it
literal — the guard follows the POSIX four, over-blocking the ksh-only
read (round-6 M4).

Word splitting then follows the POSIX order — field splitting happens
before quote removal, per word — so a quoted word containing separators
(``echo 'safe; make prod-down'``) stays one inert word while ``make
'prod-down'`` yields the plain name. Backslash outside quotes escapes the
next character; inside double quotes it escapes only ``" \\ $ ` `` (POSIX);
``\\<newline>`` continuations never arrive here — _segments drops them
wherever the five shells join them (non-single-quote contexts and backtick
spans alike, round-7 C1), and inside single-quote regions outside a
backtick span they stay literal like the shells keep them (round-6 M2;
ksh's ``\\'``-escapes-inside-a-body divergence is over-blocked there,
round-6 M4);
``$"…"`` (locale) drops the ``$`` and behaves like its base quote kind;
concatenated quoting (``pr"od-down"``) yields one word. ``$'…'`` ANSI-C
quoting is the registered known gap (round-6 I5): bash/zsh/ksh evaluate
its ``\\x…``/``\\ooo`` escapes, so ``$'\\x70kill'`` really runs pkill in
four of five shells while the splitter keeps the backslash literal and
the denylist never sees the evaluated name — same "unresolved text" gap
family as variables, accepted because closing it means decoding escapes
before matching. Port of command_guard.rs's split_segments / split_words
without the paren-depth cwd tracking (the denylist is depth-independent).
"""

from __future__ import annotations

__all__ = ["UnclosedShellConstruct", "_segments", "_shell_split_words"]

# Segment separators outside quotes. CR is included so the segmentation set
# MATCHES the word splitter's whitespace set: ``str.isspace`` accepts CR/VT/
# FF as blanks, and a splitter/segmenter disagreement let interactive-shell
# CR line breaks hide a whole second command inside one segment (attack
# report #707 MEDIUM-9). VT/FF need no separator entry: both machines treat
# them as word blanks only, so the word they sit inside stays one literal
# (real shells do the same outside interactive readline).
_SEGMENT_SEPARATORS = ("|", "&", ";", "`", "\n", "\r")


class UnclosedShellConstruct(Exception):
    """The text ends inside an unterminated quote or command substitution;
    ``reason`` names which. Malformed text is refused instead of parsed
    (round-3 H1): see _segments for the ksh-vs-POSIX rationale. Plain
    Exception subclass — the guard catches it and re-raises its own
    TerminalCommandBlockedError with the reason, keeping this module free
    of a circular import on the denylist module."""


def _segments(command: str) -> list[str]:
    """Split shell text into simple-command segments (quote-aware). Raises
    UnclosedShellConstruct (a TerminalCommandBlockedError subclass carrying
    the reason) when the text ends inside an unterminated quote or command
    substitution — malformed-即拦 (round-3 H1): escape sequences inside an
    unterminated substitution body mean different things to different
    shells (ksh 93u+ runs what POSIX/bash reject as a parse error — verified
    live), so no single parse of the text can be right and the whole family
    is refused. This retracts the round-2 ``$(x\\)`` ALLOW flip: that form
    is escape-ambiguous the same way. An unterminated construct is a parse
    error in EVERY shell, so the only inputs lost are malformed ones the
    user has to rewrite anyway."""
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    # Open command-substitution/paren bodies as (restore_double, body_is_
    # backtick) frames. restore_double: a body opened inside ``"…"`` must
    # restore the double-quote state when it closes — ``)`` and the closing
    # backtick pop the same stack. body_is_backtick (round-7 C1): a
    # backtick body is re-lexed with backtick rules by every shell, where
    # ``\\<newline>`` is a continuation everywhere in the span — ``'…'``
    # regions and nested ``$(…)`` bodies included — so any open backtick
    # frame re-enables the continuation branch.
    bodies: list[tuple[bool, bool]] = []
    chars = iter(command)
    lookahead = None
    while True:
        char = lookahead or next(chars, None)
        lookahead = None
        if char is None:
            if in_single:
                raise UnclosedShellConstruct("unclosed single quote")
            if in_double:
                raise UnclosedShellConstruct("unclosed double quote")
            if bodies:
                raise UnclosedShellConstruct("unclosed command substitution")
            break
        if char == "\\" and (not in_single or any(frame[1] for frame in bodies)):
            # A backslash pairs with the next character everywhere EXCEPT
            # inside single quotes — plain ``'…'`` AND ``$'…'`` ANSI-C keep
            # ``\\<newline>`` (and every other backslash) literal, matching
            # every shell — and "inside single quotes" is tracked by
            # in_single ALONE: a substitution never opens inside a
            # single-quote region (the region consumes every character up
            # to its closing quote), so in_single is always False when a
            # ``$(``/backtick body starts, and a ``'`` inside the body is a
            # FRESH quoting context there. Round-5's condition also OR-ed
            # resume_double in, which overrode the literal rule inside
            # substitution bodies (`echo "$(make 'prod-\\<NL>down')"` was
            # misread as a continuation while all five shells keep it
            # literal — round-6 M2) and let ``'a\\'`` inside a body swallow
            # the closing quote and hide a real kill (ksh really runs it —
            # round-6 M4). The exception (round-7 C1): a BACKTICK body is
            # re-lexed with backtick rules, where ``\\<newline>`` joins the
            # line in ``'…'`` regions too — all five shells really ran
            # ``echo `make 'prod-\\<NL>down'` `` while in_single hid it —
            # so any open backtick frame overrides the literal rule. Other
            # backslash pairs stay literal there (verified: ``'a\\;b'``,
            # ``'a\\$b'``, ``'a\\\\b'`` keep their backslashes in five
            # shells — only ``\\<newline>`` joins). The pair travels into
            # the segment text RAW so the splitter (same pairing) re-merges
            # the word; the consequences this yields, per context: an
            # escaped separator is data (`echo safe\\; make prod-down` is
            # one echo — round-4 P2), an escaped ``)``/backtick/``"``
            # cannot close its construct (a substitution closed at ``\\)``
            # drowned the payload after it in a quoted word while shells
            # ran it — review #707 R3), and ``\\<newline>`` is a line
            # continuation, BOTH characters dropped — unquoted,
            # double-quoted, ``$"…"`` and $()/backtick bodies alike
            # (round-5 C1/C2: `make "prod-\\<NL>down"` really ran in all
            # five shells). A backslash at EOF pairs with nothing and
            # carries no state (bash/dash keep it literal, zsh/sh/ksh drop
            # it — equally harmless).
            if (escaped := next(chars, None)) is not None and escaped != "\n":
                current.append(char)
                current.append(escaped)
            continue
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
                    bodies.append((True, False))
                    in_double = False
                    lookahead = None
                else:
                    current.append(char)
            elif char == "`":
                segments.append("".join(current).strip())
                current = []
                bodies.append((True, True))
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
                bodies.append((False, False))
                lookahead = None
            else:
                current.append(char)
        elif char == "(":
            segments.append("".join(current).strip())
            current = []
            bodies.append((False, False))
        elif char in (")", "`"):
            # ``)`` closes a ``$(…)``/paren body; a backtick outside quotes
            # either CLOSES an open backtick body (the innermost frame is a
            # backtick one) or OPENS a bare one at top level / inside a
            # $() body — pushing the frame whose kind re-enables ``'…'``
            # continuations there (round-7 C1). Both closers pop the same
            # stack, else a stale True leaks and the host string's closing
            # quote is misread as opening one, drowning `; make prod-down`
            # in the quoted word (#707 C-1); any frame left open at EOF is
            # the round-3 H1 refusal.
            segments.append("".join(current).strip())
            current = []
            if char == "`" and not (bodies and bodies[-1][1]):
                bodies.append((False, True))
            elif bodies and bodies.pop()[0]:
                in_double = True
        elif char in _SEGMENT_SEPARATORS:
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    segments.append("".join(current).strip())
    return [segment for segment in segments if segment]


def _shell_split_words(text: str) -> list[str]:
    """Split one segment into words the way a shell does, THEN quote-remove
    per word (POSIX order: field splitting happens before quote removal)."""
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
            # Known gap (round-6 I5): ANSI-C escape EVALUATION is not done —
            # `$'\\x70kill'` becomes pkill in bash/zsh/sh/ksh while this
            # splitter keeps the backslash literal, so the denylist never
            # sees the evaluated name (registered with the variable-gap
            # family in terminal_guard.py).
            index += 1
        elif char.isspace():
            flush()
            index += 1
        else:
            current.append(char)
            index += 1
    flush()
    return words
