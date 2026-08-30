"""Built-in Pi skill sources and lock, transcribed from the retired tracked files.

``config/skills.yaml`` / ``config/skills.lock`` are retired: the authoritative
skill source declarations (``{repo, ref}`` per skill) and the resolved lock
(``+ commit``) now live in the ``global_settings`` table under the
``skill_sources`` / ``skill_lock`` keys (see
``server.app.services.skill_source_store``). These constants are the seed used
when the DB has no ``skill_sources`` row and no legacy file exists to import;
once seeded, the documents are managed through the DB only.
"""

from __future__ import annotations

from server.app.skills.config import (
    SkillsConfig,
    SkillsLock,
    SkillSourceConfig,
)

_REPO_PREFIX = "~/.agents/skills"

# Demo workflow (education_video_problems_generation) skills. The repos are
# created on the local machine by ``make import-demo`` (git init + tag
# v1.0.0), so their commits are intentionally NOT pinned in
# BUILTIN_SKILL_LOCK below: the first dispatch or ``make skills-lock``
# resolves and locks them (lock entries for absent repos fail with guidance
# pointing at ``make import-demo``).
_DEMO_SKILL_REF = "v1.0.0"
_DEMO_SKILL_SOURCES: dict[str, SkillSourceConfig] = {
    f"education-video-problems-generation/{name}": SkillSourceConfig(
        repo=f"{_REPO_PREFIX}/education-video-problems-generation/{name}",
        ref=_DEMO_SKILL_REF,
    )
    for name in ("write-script", "review-script", "generate-questions", "review-questions")
}

BUILTIN_SKILL_SOURCES = SkillsConfig(skills={**_DEMO_SKILL_SOURCES})

# Demo skill repos are machine-local (created by ``make import-demo``), so no
# commit can be pinned at authoring time; the lock starts empty and the first
# relock fills it.
BUILTIN_SKILL_LOCK = SkillsLock(
    version="1",
    resolved_at="2026-08-07T01:44:10Z",
    skills={},
)
