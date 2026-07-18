from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_FILES = [
    "server/app/worker.py",
    "server/app/worker_thread.py",
    "server/app/worker_scheduler.py",
    "server/app/worker_candidates.py",
    "server/app/workflows/executor.py",
]

RETIRED_MODULE_FRAGMENTS = [
    "server.app.worker import",
    "server.app.worker_thread",
    "server.app.worker_scheduler",
    "server.app.worker_candidates",
    "server.app.workflows.executor",
]

# Relative imports only refer to the retired top-level module when they sit at
# the matching package depth (e.g. server/app/routes/__init__.py legitimately
# does `from .worker import` for routes/worker.py, which is not retired).
RETIRED_RELATIVE_FRAGMENTS = {
    1: ["from .worker import"],
    2: ["from ..worker import"],
}


def test_legacy_worker_files_stay_deleted():
    for rel in RETIRED_FILES:
        assert not (ROOT / rel).exists(), f"legacy file resurrected: {rel}"


def test_no_import_of_retired_worker_modules():
    offenders: list[str] = []
    app_root = ROOT / "server" / "app"
    for path in sorted(app_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        fragments = list(RETIRED_MODULE_FRAGMENTS)
        depth = len(path.relative_to(app_root).parts)
        fragments.extend(RETIRED_RELATIVE_FRAGMENTS.get(depth, []))
        for fragment in fragments:
            if fragment in text:
                offenders.append(f"{path.relative_to(ROOT)}: contains {fragment!r}")
    assert not offenders, "legacy worker imports resurrected:\n" + "\n".join(offenders)


# TODO(Task 5): uncomment once handwritten VideoItem references are removed.
# def test_frontend_has_no_handwritten_video_item_type():
#     offenders: list[str] = []
#     for path in sorted((ROOT / "frontend" / "src").rglob("*")):
#         if path.suffix not in {".ts", ".tsx"}:
#             continue
#         text = path.read_text(encoding="utf-8")
#         if "VideoItem" in text:
#             offenders.append(str(path.relative_to(ROOT)))
#     assert not offenders, "handwritten VideoItem references remain:\n" + "\n".join(offenders)
