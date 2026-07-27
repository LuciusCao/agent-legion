"""Local user authentication: passwords, sessions, and login rate limiting.

Session tokens are bearer secrets; only their sha256 digest is persisted so a
database leak does not expose usable credentials (SECURITY-AUTH-001).
"""
