from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage (random salt per call)."""
    if not password:
        raise ValueError("Password is required")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Check a plaintext password against a stored hash, constant-time.

    Unknown users (``stored_hash is None``) still pay the hashing cost so the
    response time does not reveal whether the username exists.
    """
    candidate = password.encode("utf-8")
    if not stored_hash:
        hashlib.pbkdf2_hmac("sha256", candidate, b"\x00" * _SALT_BYTES, _ITERATIONS)
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", candidate, bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)
