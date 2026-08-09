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
    LockedSkillSource,
    SkillsConfig,
    SkillsLock,
    SkillSourceConfig,
)

_REPO_PREFIX = "~/.agents/skills/agent-legion"

BUILTIN_SKILL_SOURCES = SkillsConfig(
    skills={
        "question_comprehension_info/assess_comprehension_difficulty": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/assess_comprehension_difficulty",
            ref="v1.1.9",
        ),
        "question_comprehension_info/generate_key_info": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/generate_key_info",
            ref="v1.4.1",
        ),
        "question_comprehension_info/generate_possible_errors": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/generate_possible_errors",
            ref="v1.4.1",
        ),
        "question_comprehension_info/review_key_info": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/review_key_info",
            ref="v1.1.13",
        ),
        "question_comprehension_info/review_possible_errors": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/review_possible_errors",
            ref="v1.3.7",
        ),
        "video_knowledge/generate_chapters": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/video_knowledge/generate_chapters",
            ref="v1.0.2",
        ),
        "video_knowledge/generate_interactions": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/video_knowledge/generate_interactions",
            ref="v1.0.3",
        ),
        "video_knowledge/review_subtitles": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/video_knowledge/review_subtitles",
            ref="v1.0.3",
        ),
        "video_knowledge/review_video_content": SkillSourceConfig(
            repo=f"{_REPO_PREFIX}/video_knowledge/review_video_content",
            ref="v1.0.4",
        ),
    }
)

BUILTIN_SKILL_LOCK = SkillsLock(
    version="1",
    resolved_at="2026-08-07T01:44:10Z",
    skills={
        "question_comprehension_info/assess_comprehension_difficulty": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/assess_comprehension_difficulty",
            ref="v1.1.9",
            commit="401b7ee4f20149151731e56dfc7f65d7d0bbdf57",
        ),
        "question_comprehension_info/generate_key_info": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/generate_key_info",
            ref="v1.4.1",
            commit="42356b845038780016d28e49a9e99bea1c685ec0",
        ),
        "question_comprehension_info/generate_possible_errors": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/generate_possible_errors",
            ref="v1.4.1",
            commit="c728443368f13cc07e91c29b8ba1cfeae125f7ed",
        ),
        "question_comprehension_info/review_key_info": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/review_key_info",
            ref="v1.1.13",
            commit="ecb102f108f09d3ae749cc8774de32690492a8ff",
        ),
        "question_comprehension_info/review_possible_errors": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/question_comprehension_info/review_possible_errors",
            ref="v1.3.7",
            commit="12d69ba85f6fd5d097f3eeac1121e6292c796bdc",
        ),
        "video_knowledge/generate_chapters": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/video_knowledge/generate_chapters",
            ref="v1.0.2",
            commit="957768e8e0e0ed731f3e07ac0111f961d8f42ae9",
        ),
        "video_knowledge/generate_interactions": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/video_knowledge/generate_interactions",
            ref="v1.0.3",
            commit="7e51c9880a03f15219163a544716e8b9247f2e93",
        ),
        "video_knowledge/review_subtitles": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/video_knowledge/review_subtitles",
            ref="v1.0.3",
            commit="52115e1d932c23b672405fe83811e334d60bf439",
        ),
        "video_knowledge/review_video_content": LockedSkillSource(
            repo=f"{_REPO_PREFIX}/video_knowledge/review_video_content",
            ref="v1.0.4",
            commit="af37359538553cc1ccdf04292079abc19efac221",
        ),
    },
)
