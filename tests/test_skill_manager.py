from __future__ import annotations

from server.app.skills.config import SkillsConfig


def test_skills_config_parses_minimal() -> None:
    data = {
        "skills": {
            "reading_analysis": {"repo": "https://example.com/skills.git", "ref": "main"}
        }
    }
    config = SkillsConfig.model_validate(data)
    assert config.skills["reading_analysis"].repo == "https://example.com/skills.git"
    assert config.skills["reading_analysis"].ref == "main"
