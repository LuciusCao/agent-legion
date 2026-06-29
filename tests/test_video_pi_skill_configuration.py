from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

VIDEO_SKILLS = {
    "video_knowledge/review_subtitles": {
        "repo": "file:///Users/user/.openclaw/workspace/skills/review-subtitles",
        "ref": "v1.0.0",
        "commit": "b232393bb5e4c6082ffc8f3afa36579d1782ae96",
    },
    "video_knowledge/generate_chapters": {
        "repo": "file:///Users/user/.openclaw/workspace/skills/slice-chapters",
        "ref": "v1.0.0",
        "commit": "6c783fbf8c99ac2bd071252612b35237ab03d8e5",
    },
    "video_knowledge/generate_interactions": {
        "repo": "file:///Users/user/.openclaw/workspace/skills/generate-interactions",
        "ref": "v1.0.0",
        "commit": "0be1c695332aab28cfd74e3631aa149d1ce1e7a8",
    },
    "video_knowledge/review_video_content": {
        "repo": "file:///Users/user/.openclaw/workspace/skills/review-interactions",
        "ref": "v1.0.0",
        "commit": "e40f75329921686d54872d5568a7d58eb26bbe6c",
    },
}


def test_video_pi_skills_are_declared_and_locked() -> None:
    skills = yaml.safe_load((ROOT / "config/skills.yaml").read_text(encoding="utf-8"))["skills"]
    lock = yaml.safe_load((ROOT / "config/skills.lock").read_text(encoding="utf-8"))["skills"]

    for key, expected in VIDEO_SKILLS.items():
        assert skills[key] == {"repo": expected["repo"], "ref": expected["ref"]}
        assert lock[key] == expected
