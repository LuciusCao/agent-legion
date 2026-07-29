from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Skill repos are declared as machine-independent `~/...` paths (expanded per
# user at resolve time), so they are pinned by their path suffix instead of a
# full path.
VIDEO_SKILLS = {
    "video_knowledge/review_subtitles": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/review_subtitles",
        "ref": "v1.0.2",
        "commit": "b5cbf8b0b3aef150facda444630dd4a198485842",
    },
    "video_knowledge/generate_chapters": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/generate_chapters",
        "ref": "v1.0.1",
        "commit": "81e9a58a4fa3d0ef0d7b677e34708c6accfe0816",
    },
    "video_knowledge/generate_interactions": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/generate_interactions",
        "ref": "v1.0.2",
        "commit": "afb50c54ec0f1f87851c39debe306a3e027c07b4",
    },
    "video_knowledge/review_video_content": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/review_video_content",
        "ref": "v1.0.3",
        "commit": "b319ca1fa1a93d14b1c59469655ff0aaeaecc6a5",
    },
}


def test_video_pi_skills_are_declared_and_locked() -> None:
    skills = yaml.safe_load((ROOT / "config/skills.yaml").read_text(encoding="utf-8"))["skills"]
    lock = yaml.safe_load((ROOT / "config/skills.lock").read_text(encoding="utf-8"))["skills"]

    for key, expected in VIDEO_SKILLS.items():
        assert set(skills[key]) == {"repo", "ref"}
        assert skills[key]["repo"].endswith(expected["repo_suffix"])
        assert skills[key]["ref"] == expected["ref"]
        assert set(lock[key]) == {"repo", "ref", "commit"}
        assert lock[key]["repo"].endswith(expected["repo_suffix"])
        assert lock[key]["ref"] == expected["ref"]
        assert lock[key]["commit"] == expected["commit"]
