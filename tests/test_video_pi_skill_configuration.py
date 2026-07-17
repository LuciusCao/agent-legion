from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

VIDEO_SKILLS = {
    "video_knowledge/review_subtitles": {
        "repo": "file:///Users/user/.agents/skills/agent-legion/video_knowledge/review_subtitles",
        "ref": "v1.0.2",
        "commit": "b5cbf8b0b3aef150facda444630dd4a198485842",
    },
    "video_knowledge/generate_chapters": {
        "repo": "file:///Users/user/.agents/skills/agent-legion/video_knowledge/generate_chapters",
        "ref": "v1.0.1",
        "commit": "81e9a58a4fa3d0ef0d7b677e34708c6accfe0816",
    },
    "video_knowledge/generate_interactions": {
        "repo": "file:///Users/user/.agents/skills/agent-legion/video_knowledge/generate_interactions",
        "ref": "v1.0.1",
        "commit": "d1314b8bade2edb3a7ee634d53ba020e3d052596",
    },
    "video_knowledge/review_video_content": {
        "repo": "file:///Users/user/.agents/skills/agent-legion/video_knowledge/review_video_content",
        "ref": "v1.0.1",
        "commit": "9fa830e36370dfe7e1e3dcc48c01b0c3d086802b",
    },
}


def test_video_pi_skills_are_declared_and_locked() -> None:
    skills = yaml.safe_load((ROOT / "config/skills.yaml").read_text(encoding="utf-8"))["skills"]
    lock = yaml.safe_load((ROOT / "config/skills.lock").read_text(encoding="utf-8"))["skills"]

    for key, expected in VIDEO_SKILLS.items():
        assert skills[key] == {"repo": expected["repo"], "ref": expected["ref"]}
        assert lock[key] == expected
