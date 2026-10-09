"""#755: the stderr-tail redaction invariant of shared/pi_events.py.

The capture keeps raw stderr in a bounded buffer and redacts it once at the
end; the first ``margin`` characters of a trimmed buffer are lookback only
(see ``scan_and_compress_pi_events``). The randomized test drives that
contract with secrets and noise drawn from disjoint alphabets, so any
secret substring found on an output face is a leak, never a coincidence.
"""

import json
import random

import pytest

from shared.pi_events import scan_and_compress_pi_events
from shared.redaction import SecretRedactor
from shared.stderr_tail import STDERR_TAIL_BYTES
from tests.helpers.secret_spans import literal_spans

pytestmark = pytest.mark.no_db

_SECRET_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_NOISE_ALPHABET = "abcdefghijklmnopqrstuvwxyz噪音日志 "
_LEAK_PROBE_CHARS = 8


def _scan(tmp_path, stderr_text: str, *secrets: str):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n' + stderr_text, encoding="utf-8")
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redactor=SecretRedactor(literal_spans(*secrets), max(len(secret) for secret in secrets)),
    )
    sink_bytes = sink.read_bytes() if sink.exists() else b""
    return tail, sink_bytes, events.read_bytes()


def _assert_no_leak(faces, secrets) -> None:
    for face in faces:
        assert len(face) <= STDERR_TAIL_BYTES
        text = face.decode("utf-8", "replace")
        for secret in secrets:
            for start in range(0, len(secret) - _LEAK_PROBE_CHARS + 1):
                probe = secret[start : start + _LEAK_PROBE_CHARS]
                if "\n" in probe:
                    continue
                assert probe not in text, f"secret fragment {probe!r} leaked"


def test_secret_longer_than_tail_budget_never_leaks_a_fragment(tmp_path):
    """codex R11 P1 复现几何：12 KiB 注册密钥前置 11 KiB stderr——旧实现的
    淘汰阶段复用了自带 8 KiB 保尾截断的回调，密钥整值尚未匹配就被截掉
    头部，约 8 KiB 残段明文落三面。"""
    rng = random.Random(11)
    secret = "\n".join("".join(rng.choice(_SECRET_ALPHABET) for _ in range(64)) for _ in range(190))
    assert len(secret) > 12_000
    stderr = ("x" * 99 + "\n") * 110 + secret + "\npanic: real cause\n"
    tail, sink, events = _scan(tmp_path, stderr, secret)
    _assert_no_leak((tail, sink, events), [secret])
    assert tail.endswith(b"panic: real cause")


def test_secret_prefixed_by_shorter_secret_is_redacted_whole(tmp_path):
    """A registered secret that is a prefix of a longer multi-line one (a
    leaf cert inside a chain) must not be matched on an INCOMPLETE
    occurrence of the long one: matching it early rewrites the long one's
    head, so the long one can never match and its tail lines leak. The
    buffer stays raw until one redaction pass over complete text, and
    nested spans merge."""
    short = "\n".join(["-----BEGIN CERT-----", "LEAF0123456789" * 4, "-----END CERT-----"])
    long = short + "\n" + "\n".join(f"CHAIN{index:04d}" + "Q" * 60 for index in range(200))
    stderr = ("n" * 99 + "\n") * 100 + long + "\npanic: boom\n"
    tail, sink, _ = _scan(tmp_path, stderr, short, long)
    _assert_no_leak((tail, sink), [short, long])
    assert tail.endswith(b"panic: boom")


def test_redaction_widens_window_start_to_straddling_secret(tmp_path):
    """Untrimmed buffer, final cut inside a secret: the window start moves
    back to the span start, so the secret is replaced instead of cut."""
    secret = "S" * 40 + "ECRET" * 20
    head = "a" * 200
    stderr = head + secret + "b" * (STDERR_TAIL_BYTES - 50) + "\n"
    tail, sink, _ = _scan(tmp_path, stderr, secret)
    _assert_no_leak((tail, sink), [secret])
    assert tail.startswith(b"***b")


def _random_secret(rng: random.Random) -> str:
    shape = rng.choice(("line", "pem", "pem-trailing-newline"))
    if shape == "line":
        return "".join(rng.choice(_SECRET_ALPHABET) for _ in range(rng.randint(9, 3000)))
    lines = [
        "".join(rng.choice(_SECRET_ALPHABET) for _ in range(rng.randint(8, 80)))
        for _ in range(rng.randint(2, 400))
    ]
    pem = "\n".join(["-----BEGIN KEY-----", *lines, "-----END KEY-----"])
    return pem + "\n" if shape == "pem-trailing-newline" else pem


def _random_noise(rng: random.Random, total: int) -> str:
    """Noise lines summing to about ``total`` characters (mostly short lines,
    occasionally one very long line) — the shape that moves trim points
    across multi-line secrets."""
    lines: list[str] = []
    while total > 0:
        length = rng.randint(200, 9000) if rng.random() < 0.05 else rng.randint(0, 120)
        lines.append("".join(rng.choice(_NOISE_ALPHABET) for _ in range(min(length, total))))
        total -= length + 1
    return "".join(line + "\n" for line in lines)


@pytest.mark.parametrize("seed", range(60))
def test_randomized_streams_never_leak_registered_secrets(tmp_path, seed):
    """Random streams of noise, JSON events and secrets (single-line,
    multi-line with/without trailing newline, prefix pairs, CJK noise, very
    long lines), with each secret preceded by a random amount of noise so
    trims land anywhere relative to it — caller contract held
    (``redactor.max_chars`` = longest literal). No face may carry any 8-char
    fragment of any secret, and the newest noise line survives."""
    rng = random.Random(seed)
    secrets = [_random_secret(rng) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.3:
        secrets.append(secrets[0] + "\n" + _random_secret(rng))
    budget = STDERR_TAIL_BYTES + max(len(secret) for secret in secrets)
    pieces: list[str] = []
    for _ in range(rng.randint(1, 5)):
        pieces.append(_random_noise(rng, rng.randint(0, 2 * budget)))
        if rng.random() < 0.2:
            pieces.append(json.dumps({"type": "turn_end", "n": rng.randint(0, 9)}) + "\n")
        inline = rng.random() < 0.3
        pieces.append(("prefix " if inline else "") + rng.choice(secrets) + "\n")
    pieces.append(_random_noise(rng, rng.randint(0, budget)) + "final marker line\n")
    tail, sink, events = _scan(tmp_path, "".join(pieces), *secrets)
    _assert_no_leak((tail, sink, events), secrets)
    assert tail.endswith(b"final marker line")
