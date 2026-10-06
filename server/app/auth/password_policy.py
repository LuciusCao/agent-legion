"""新密码基线策略（#970）：最小长度 + 常见弱口令拒绝。

只作用于「设置新密码」的入口——bootstrap（含 env 种子）、管理员建用户、
管理员重置密码；登录校验从不经过这里，策略收紧不会让已有账号无法登录。

弱口令表是刻意保持很小的内置列表（不引入外部字典依赖），覆盖最常见的
默认口令与键盘序列；比较时忽略大小写与首尾空白。
"""

from __future__ import annotations

MIN_PASSWORD_LENGTH = 12

_COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890ab",
        "1q2w3e4r5t6y",
        "abc123456789",
        "admin1234567",
        "administrator",
        "adminadmin123",
        "changeme1234",
        "iloveyou1234",
        "letmein12345",
        "password1234",
        "password12345",
        "password123456",
        "passw0rd1234",
        "qwerty123456",
        "qwertyuiop12",
        "qwertyuiopasdf",
        "welcome12345",
        "agentlegion123",
    }
)


class WeakPasswordError(ValueError):
    """A new password that fails the baseline policy; message is user-facing."""


def validate_new_password(password: str) -> None:
    """Raise WeakPasswordError when ``password`` fails the baseline.

    Reads ``MIN_PASSWORD_LENGTH`` at call time (module attribute), so the
    test harness can relax the length for fixture accounts the same way it
    cheapens the pbkdf2 cost.

    The length floor counts the password with surrounding whitespace
    removed: leading/trailing spaces add no guessing cost, and a password
    made only of whitespace must never pass. The stored secret is still the
    exact string given (login compares it verbatim), and the policy only
    runs on new-password paths, so existing accounts are unaffected.
    """
    normalized = password.strip().lower()
    if not normalized or len(normalized) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    # A single repeated character ("111111111111") is as weak as any listed entry.
    if normalized in _COMMON_PASSWORDS or len(set(normalized)) == 1:
        raise WeakPasswordError("Password is too common; choose a less guessable one")
