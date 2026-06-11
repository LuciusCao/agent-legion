import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.verify_specs import (
    ARCHIVE_BANNER,
    check_refs,
    classify_spec,
    extract_refs,
    inject_banner,
    move_spec,
    parse_status,
)


def test_extract_refs_finds_code_paths():
    content = """
位于 `server/app/routes/videos.py` 的视频路由。
前端在 `frontend/src/pages/ListPage.tsx` 中调用。
配置见 `config/pipeline.yaml`。
"""
    refs = extract_refs(content)
    assert "server/app/routes/videos.py" in refs
    assert "frontend/src/pages/ListPage.tsx" in refs
    assert "config/pipeline.yaml" in refs


def test_extract_refs_ignores_urls():
    content = """
See https://example.com/server/app/main.py for docs.
Local path: `server/app/main.py`.
"""
    refs = extract_refs(content)
    assert len(refs) == 1
    assert refs[0] == "server/app/main.py"


def test_extract_refs_deduplicates():
    content = "`server/app/main.py` 和 `server/app/main.py`"
    refs = extract_refs(content)
    assert refs == ["server/app/main.py"]


def test_extract_refs_supports_tests_dir():
    content = "`tests/test_api.py` 覆盖此接口"
    refs = extract_refs(content)
    assert "tests/test_api.py" in refs


def test_parse_status_chinese_completed():
    assert parse_status("**状态**: 已完成\n") == "已完成"


def test_parse_status_chinese_pending():
    assert parse_status("**状态**: 待批准\n") == "待批准"


def test_parse_status_english():
    assert parse_status("Status: completed\n") == "completed"


def test_parse_status_default():
    assert parse_status("# Some Title\n") == "已完成"


def test_check_refs_with_existing_and_missing(tmp_path: Path):
    root = tmp_path
    (root / "server").mkdir()
    (root / "server" / "app.py").write_text("pass")

    existing, missing = check_refs(["server/app.py", "server/gone.py"], root)
    assert existing == ["server/app.py"]
    assert missing == ["server/gone.py"]


def test_classify_spec_active_with_missing_refs(tmp_path: Path):
    spec = tmp_path / "test-design.md"
    spec.write_text("**状态**: 进行中\n\nSee `server/gone.py`.\n")
    result = classify_spec(spec, tmp_path)
    assert result["target_dir"] == "specs"
    assert result["missing"] == ["server/gone.py"]


def test_classify_spec_completed_healthy(tmp_path: Path):
    spec = tmp_path / "test-design.md"
    spec.write_text("**状态**: 已完成\n\nSee `server/app.py`.\n")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "app.py").write_text("pass")
    result = classify_spec(spec, tmp_path)
    assert result["target_dir"] == "completed"
    assert result["missing"] == []


def test_classify_spec_completed_stale(tmp_path: Path):
    spec = tmp_path / "test-design.md"
    spec.write_text("**状态**: 已完成\n\nSee `server/gone.py`.\n")
    result = classify_spec(spec, tmp_path)
    assert result["target_dir"] == "archive"


def test_classify_spec_deprecated(tmp_path: Path):
    spec = tmp_path / "test-design.md"
    spec.write_text("**状态**: 已废弃\n")
    result = classify_spec(spec, tmp_path)
    assert result["target_dir"] == "archive"


def test_inject_banner_idempotent(tmp_path: Path):
    spec = tmp_path / "test.md"
    spec.write_text("# Title\n\nBody\n")
    assert inject_banner(spec) is True
    content = spec.read_text()
    assert ARCHIVE_BANNER.strip() in content
    assert inject_banner(spec) is False


def test_move_spec_git_mv(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    src = repo / "spec.md"
    src.write_text("# Spec\n")
    dst_dir = repo / "archive"
    dst = move_spec(src, dst_dir)
    assert dst == dst_dir / "spec.md"
    assert dst.exists()
    assert not src.exists()
