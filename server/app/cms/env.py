"""Arbitration between authoritative ``CMS_*`` env vars and ``BASECMS_*`` aliases.

Open-source de-identification (D3): ``CMS_*`` is the authoritative prefix;
the ``BASECMS_*`` names are deprecated aliases kept for the transition
window. The semantics mirror the database URL arbitration (config governance
G4): exactly one name set wins (alias-only logs a deprecation warning); both
set to the same value are accepted silently; both set with different values
raise ``ValueError`` naming the authoritative ``CMS_*`` variable to keep.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Authoritative name -> deprecated alias.
CMS_ENV_ALIASES: dict[str, str] = {
    "CMS_TOKEN": "BASECMS_TOKEN",
    "CMS_BASE_URL": "BASECMS_BASE_URL",
    "CMS_APP_ID": "BASECMS_APP_ID",
    "CMS_NONCE": "BASECMS_NONCE",
    "CMS_SECRET": "BASECMS_SECRET",
    "CMS_TOKEN_URL": "BASECMS_TOKEN_URL",
}

# Deprecation warnings fire once per process per alias; runtime resolution
# happens on every CMS call and must not spam the logs.
_warned_aliases: set[str] = set()


def resolve_cms_env(primary: str) -> str | None:
    """Resolve an authoritative ``CMS_*`` env var against its deprecated alias.

    ``primary`` must be a key of :data:`CMS_ENV_ALIASES`. Empty values count
    as unset. Returns the winning value, or ``None`` when neither name is set.
    """
    alias = CMS_ENV_ALIASES[primary]
    primary_value = os.environ.get(primary) or None
    alias_value = os.environ.get(alias) or None
    if primary_value and alias_value and primary_value != alias_value:
        raise ValueError(
            f"{primary} and {alias} are both set with different values. "
            f"{alias} is a deprecated alias (de-identification D3): unset it "
            f"and keep only {primary}."
        )
    if alias_value and not primary_value and alias not in _warned_aliases:
        _warned_aliases.add(alias)
        logger.warning("%s is deprecated; rename it to %s (same value)", alias, primary)
    return primary_value or alias_value


def validate_cms_env_aliases() -> None:
    """Fail fast on ``CMS_*``/``BASECMS_*`` conflicts at startup.

    Resolving every CMS env key surfaces alias deprecation warnings and
    conflicting dual assignments before any runtime CMS call.
    """
    for primary in CMS_ENV_ALIASES:
        resolve_cms_env(primary)
