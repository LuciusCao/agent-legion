class SkillConfigError(ValueError):
    """Raised when skill configuration is missing or invalid."""


class SkillRepoError(RuntimeError):
    """Raised when git clone/fetch/checkout fails."""


class SkillPathError(ValueError):
    """Raised when a skill key escapes the allowed base directory."""
