import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.generate_architecture import (
    AUTO_END,
    AUTO_START,
    extract_config,
    extract_fastapi_routes,
    extract_frontend_routes,
    extract_models,
    extract_pipeline_phases,
    replace_section,
)

# ---------------------------------------------------------------------------
# replace_section
# ---------------------------------------------------------------------------


def test_replace_section_with_markers():
    content = f"# Title\n\n{AUTO_START}\nold\n{AUTO_END}\n\nfooter"
    result = replace_section(content, "new")
    assert "new" in result
    assert "old" not in result
    assert AUTO_START in result
    assert AUTO_END in result


def test_replace_section_with_todo_placeholder():
    content = (
        "# Title\n\n## API Surface / Interface\n\n<!-- TODO: 阶段 2 由 AST 自动生成 -->\n\nfooter"
    )
    result = replace_section(content, "generated")
    assert "generated" in result
    assert "TODO" not in result
    assert AUTO_START in result
    assert AUTO_END in result


def test_replace_section_inserts_at_api_surface():
    content = "# Title\n\n## API Surface / Interface\n\nfooter"
    result = replace_section(content, "generated")
    assert "generated" in result
    assert AUTO_START in result
    assert AUTO_END in result


def test_replace_section_missing_heading_raises():
    content = "# Title\n\nfoo\n"
    try:
        replace_section(content, "generated")
        raise AssertionError("Expected ValueError")
    except ValueError as e:
        assert "API Surface / Interface" in str(e)


# ---------------------------------------------------------------------------
# Backend extraction
# ---------------------------------------------------------------------------


def test_extract_fastapi_routes(tmp_path: Path):
    routes_dir = tmp_path / "server" / "app" / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "videos.py").write_text("""
def create_videos_router():
    router = APIRouter(prefix="/videos")

    @router.get("")
    def list_videos():
        pass

    @router.post("/{video_id}")
    def create_video():
        pass

    @router.post("/upload")
    async def upload_video():
        pass
""")

    result = extract_fastapi_routes(tmp_path)
    assert "list_videos" in result
    assert "upload_video" in result
    assert "`/videos`" in result
    assert "`/videos/{video_id}`" in result
    assert "`/videos/upload`" in result
    assert "POST" in result


def test_extract_models(tmp_path: Path):
    app_dir = tmp_path / "server" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "records.py").write_text("""
from typing import TypedDict

class VideoRecord(TypedDict):
    id: str
    title: str
""")

    result = extract_models(tmp_path)
    assert "VideoRecord" in result
    assert "TypedDict" in result
    assert "id: str" in result


# ---------------------------------------------------------------------------
# Frontend extraction
# ---------------------------------------------------------------------------


def test_extract_frontend_routes(tmp_path: Path):
    src_dir = tmp_path / "frontend" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "AppRoutes.tsx").write_text("""
<Route path="/" element={<ListPage />} />
<Route path="/about" element={<AboutPage />} />
""")

    result = extract_frontend_routes(tmp_path)
    assert "ListPage" in result
    assert "AboutPage" in result
    assert "`/about`" in result


# ---------------------------------------------------------------------------
# Pipeline extraction
# ---------------------------------------------------------------------------


def test_extract_pipeline_phases(tmp_path: Path):
    result = extract_pipeline_phases(tmp_path)
    assert "知识视频（8 阶段）" in result
    assert (
        result.index("`download`")
        < result.index("`transcribe`")
        < result.index("`subtitle_review`")
        < result.index("`chapter_generate`")
        < result.index("`interaction_generate`")
        < result.index("`content_review`")
        < result.index("`assemble`")
        < result.index("`package`")
    )


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------


def test_extract_config(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: data\n")
    (config_dir / "agent_legion.yaml").write_text("""
asr:
  provider: auto
openclaw:
  timeout_seconds: 600
""")
    (config_dir / "workflow.yaml").write_text("""
workflows:
  enabled: true
""")

    result = extract_config(tmp_path)
    assert "`asr`" in result
    assert "`openclaw`" in result
