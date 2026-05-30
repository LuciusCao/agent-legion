import json
from pathlib import Path

from server.app.services.interaction_stats import (
    compute_interaction_review_status,
    compute_interaction_stats,
)


def test_compute_interaction_stats_no_interactions(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    assert compute_interaction_stats(video_dir) is None


def test_compute_interaction_stats_empty_interactions(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    assert compute_interaction_stats(video_dir) is None


def test_compute_interaction_stats_without_review(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                    {"id": "n3", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    stats = compute_interaction_stats(video_dir)
    assert stats == {
        "example_practice": {"passed": 0, "total": 2},
        "interaction_summary": {"passed": 0, "total": 1},
    }


def test_compute_interaction_stats_with_global_published(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                    {"id": "n3", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )
    stats = compute_interaction_stats(video_dir)
    assert stats == {
        "example_practice": {"passed": 2, "total": 2},
        "interaction_summary": {"passed": 1, "total": 1},
    }


def test_compute_interaction_stats_with_individual_reviews(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                    {"id": "n3", "type": "example_practice"},
                    {"id": "n4", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps(
            {
                "status": "pending_review",
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                    {"item_id": "n2", "status": "published"},
                    {"item_id": "n3", "status": "rejected"},
                    {"item_id": "n4", "status": "published"},
                ],
            }
        ),
        encoding="utf-8",
    )
    stats = compute_interaction_stats(video_dir)
    assert stats == {
        "example_practice": {"passed": 2, "total": 3},
        "interaction_summary": {"passed": 1, "total": 1},
    }


def test_compute_interaction_stats_node_review_overrides_global(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps(
            {
                "status": "published",
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                    {"item_id": "n2", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )
    stats = compute_interaction_stats(video_dir)
    assert stats == {
        "example_practice": {"passed": 1, "total": 2},
    }


def test_compute_interaction_stats_mixed_types(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "video_summary"},
                    {"id": "n3", "type": "unknown_type"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )
    stats = compute_interaction_stats(video_dir)
    assert stats == {
        "example_practice": {"passed": 1, "total": 1},
        "video_summary": {"passed": 1, "total": 1},
        "unknown_type": {"passed": 1, "total": 1},
    }


# ----- compute_interaction_review_status tests -----


def test_review_status_no_interactions(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    assert compute_interaction_review_status(video_dir) is None


def test_review_status_empty_interactions(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    assert compute_interaction_review_status(video_dir) is None


def test_review_status_no_review_file(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "n1", "type": "example_practice"}]}),
        encoding="utf-8",
    )
    assert compute_interaction_review_status(video_dir) is None


def test_review_status_all_passed_global(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )
    assert compute_interaction_review_status(video_dir) == "all_passed"


def test_review_status_all_failed_global(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "rejected"}), encoding="utf-8"
    )
    assert compute_interaction_review_status(video_dir) == "all_failed"


def test_review_status_partial(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                    {"id": "n3", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps(
            {
                "status": "pending_review",
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                    {"item_id": "n2", "status": "rejected"},
                    {"item_id": "n3", "status": "published"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert compute_interaction_review_status(video_dir) == "partial"


def test_review_status_all_passed_individual(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "interaction_summary"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps(
            {
                "reviews": [
                    {"item_id": "n1", "status": "published"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert compute_interaction_review_status(video_dir) == "all_passed"


def test_review_status_all_failed_individual(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps(
            {
                "interactions": [
                    {"id": "n1", "type": "example_practice"},
                    {"id": "n2", "type": "example_practice"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps(
            {
                "reviews": [
                    {"item_id": "n1", "status": "rejected"},
                    {"item_id": "n2", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert compute_interaction_review_status(video_dir) == "all_failed"


def test_compute_interaction_stats_invalid_json(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text("{bad json", encoding="utf-8")
    assert compute_interaction_stats(video_dir) is None


def test_compute_interaction_stats_review_not_dict(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "n1", "type": "example_practice"}]}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text("[]", encoding="utf-8")
    stats = compute_interaction_stats(video_dir)
    assert stats == {"example_practice": {"passed": 0, "total": 1}}


def test_compute_interaction_review_status_review_not_dict(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "n1", "type": "example_practice"}]}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text("[]", encoding="utf-8")
    assert compute_interaction_review_status(video_dir) is None


def test_compute_interaction_review_status_all_nodes_missing_id(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"type": "example_practice"}]}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"status": "published"}), encoding="utf-8"
    )
    assert compute_interaction_review_status(video_dir) is None


def test_compute_interaction_review_status_invalid_json(tmp_path: Path) -> None:
    video_dir = tmp_path / "v1"
    video_dir.mkdir()
    (video_dir / "interactions.json").write_text(
        json.dumps({"interactions": [{"id": "n1", "type": "example_practice"}]}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text("{bad json", encoding="utf-8")
    assert compute_interaction_review_status(video_dir) is None
