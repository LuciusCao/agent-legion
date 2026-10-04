"""Skill version writes count Unicode characters, not encoded UTF-8 bytes.

Keep the API field and committed edit reader aligned. Creation and shared
materials additionally have their own stricter byte limits.
"""

SKILL_CONTENT_MAX_CHARS = 128 * 1024
SKILL_CONTENT_MAX_UTF8_BYTES = 4 * SKILL_CONTENT_MAX_CHARS
