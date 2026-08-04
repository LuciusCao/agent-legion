from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Skill repos are declared as machine-independent `~/...` paths (expanded per
# user at resolve time), so they are pinned by their path suffix instead of a
# full path.
VIDEO_SKILLS = {
    "video_knowledge/review_subtitles": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/review_subtitles",
        "ref": "v1.0.3",
        "commit": "52115e1d932c23b672405fe83811e334d60bf439",
    },
    "video_knowledge/generate_chapters": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/generate_chapters",
        "ref": "v1.0.2",
        "commit": "957768e8e0e0ed731f3e07ac0111f961d8f42ae9",
    },
    "video_knowledge/generate_interactions": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/generate_interactions",
        "ref": "v1.0.3",
        "commit": "7e51c9880a03f15219163a544716e8b9247f2e93",
    },
    "video_knowledge/review_video_content": {
        "repo_suffix": ".agents/skills/agent-legion/video_knowledge/review_video_content",
        "ref": "v1.0.4",
        "commit": "af37359538553cc1ccdf04292079abc19efac221",
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
