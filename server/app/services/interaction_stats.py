import contextlib
import json
from pathlib import Path


def _load_interactions(video_dir: Path) -> list[dict] | None:
    """Load interactions list from interactions.json, or None if missing/invalid."""
    interactions_path = video_dir / "interactions.json"
    if not interactions_path.exists():
        return None
    try:
        data = json.loads(interactions_path.read_text(encoding="utf-8"))
        interactions = data.get("interactions", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return None
    return [n for n in interactions if isinstance(n, dict)] or None


def _load_review(video_dir: Path) -> tuple[dict[str, str], str | None] | None:
    """Load review_result.json and return (review_map, global_status), or None."""
    review_path = video_dir / "review_result.json"
    if not review_path.exists():
        return None
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(review, dict):
        return None
    review_map: dict[str, str] = {}
    global_status: str | None = review.get("status")
    for entry in review.get("reviews", []):
        if isinstance(entry, dict) and entry.get("item_id"):
            review_map[str(entry["item_id"])] = str(entry.get("status", ""))
    return review_map, global_status


def compute_interaction_review_status(video_dir: Path) -> str | None:
    """Compute overall interaction review status.

    Returns 'all_passed', 'partial', 'all_failed', or None when no interactions
    or no review result exists.
    """
    interactions = _load_interactions(video_dir)
    if not interactions:
        return None

    review_data = _load_review(video_dir)
    if review_data is None:
        return None
    review_map, global_status = review_data

    total = 0
    passed = 0
    for node in interactions:
        node_id = node.get("id")
        if node_id is None:
            continue
        total += 1
        node_status = review_map.get(str(node_id))
        status = node_status if node_status else global_status
        if status == "published":
            passed += 1

    if total == 0:
        return None
    if passed == total:
        return "all_passed"
    if passed == 0:
        return "all_failed"
    return "partial"


def compute_interaction_stats(video_dir: Path) -> dict[str, dict[str, int]] | None:
    """Compute interaction pass stats grouped by type.

    Returns a dict mapping interaction type -> {"passed": int, "total": int}
    or None if no interactions exist.
    """
    interactions = _load_interactions(video_dir)
    if not interactions:
        return None

    review_map: dict[str, str] = {}
    global_status: str | None = None
    review_path = video_dir / "review_result.json"
    if review_path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            review = json.loads(review_path.read_text(encoding="utf-8"))
            if isinstance(review, dict):
                global_status = review.get("status")
                for entry in review.get("reviews", []):
                    if isinstance(entry, dict) and entry.get("item_id"):
                        review_map[str(entry["item_id"])] = str(entry.get("status", ""))

    stats: dict[str, dict[str, int]] = {}
    for node in interactions:
        node_type = str(node.get("type", "unknown"))
        node_id = node.get("id")

        if node_type not in stats:
            stats[node_type] = {"passed": 0, "total": 0}

        stats[node_type]["total"] += 1

        node_status = review_map.get(str(node_id)) if node_id is not None else None
        status = node_status if node_status else global_status
        if status == "published":
            stats[node_type]["passed"] += 1

    return stats if stats else None
