"""scripts/import-demo.sh：示例 skill 导入脚本的幂等性与保留语义。

用 AGENT_LEGION_DEMO_SKILLS_DIR 把目标根目录指到 tmp_path，不碰用户真实的
~/.agents 目录。纯 shell 逻辑测试，不碰数据库。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "import-demo.sh"
TAG = "v1.0.0"
SKILL_NAMES = ("write-script", "review-script", "generate-questions", "review-questions")


def _run_import_demo(target_root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AGENT_LEGION_DEMO_SKILLS_DIR": str(target_root)}
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _tag_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{TAG}^{{commit}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_first_run_imports_all_skills_with_git_and_tag(tmp_path: Path) -> None:
    result = _run_import_demo(tmp_path)
    assert result.returncode == 0, result.stderr

    for name in SKILL_NAMES:
        skill_dir = tmp_path / name
        assert (skill_dir / ".git").is_dir(), name
        assert (skill_dir / "SKILL.md").is_file(), name
        assert (skill_dir / "references" / "output-contract.md").is_file(), name
        assert (skill_dir / "scripts" / "validate_output.py").is_file(), name
        _tag_commit(skill_dir)  # raises when the tag is missing
        # Source tree content is copied verbatim.
        source = (REPO_ROOT / "examples" / "skills" / name / "SKILL.md").read_text("utf-8")
        assert (skill_dir / "SKILL.md").read_text("utf-8") == source
    assert "导入 4 个，跳过 0 个" in result.stdout


def test_second_run_skips_everything(tmp_path: Path) -> None:
    assert _run_import_demo(tmp_path).returncode == 0
    second = _run_import_demo(tmp_path)
    assert second.returncode == 0, second.stderr
    assert second.stdout.count("[跳过]") == len(SKILL_NAMES)
    assert "导入 0 个，跳过 4 个" in second.stdout


def test_rerun_preserves_user_changes(tmp_path: Path) -> None:
    assert _run_import_demo(tmp_path).returncode == 0
    edited = tmp_path / "write-script" / "SKILL.md"
    edited.write_text("# user edited\n", encoding="utf-8")
    second = _run_import_demo(tmp_path)
    assert second.returncode == 0, second.stderr
    assert edited.read_text(encoding="utf-8") == "# user edited\n"


def test_existing_non_git_directory_is_left_alone(tmp_path: Path) -> None:
    stray = tmp_path / "write-script"
    stray.mkdir(parents=True)
    (stray / "SKILL.md").write_text("# not a repo\n", encoding="utf-8")

    result = _run_import_demo(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "[警告] write-script" in result.stderr
    # Not initialized, not overwritten; the other three skills import normally.
    assert not (stray / ".git").exists()
    assert stray.joinpath("SKILL.md").read_text("utf-8") == "# not a repo\n"
    assert (tmp_path / "review-script" / ".git").is_dir()
    assert "导入 3 个，跳过 1 个" in result.stdout


def test_repo_without_tag_gets_tagged(tmp_path: Path) -> None:
    """Imported once, tag deleted by hand: rerun re-tags without re-committing."""
    assert _run_import_demo(tmp_path).returncode == 0
    repo = tmp_path / "generate-questions"
    subprocess.run(["git", "-C", str(repo), "tag", "-d", TAG], check=True, capture_output=True)

    second = _run_import_demo(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "[打 tag] generate-questions" in second.stdout
    _tag_commit(repo)
