import contextlib
import json
from pathlib import Path


def compute_interaction_stats(video_dir: Path) -> dict[str, dict[str, int]] | None:
    """Compute interaction pass stats grouped by type.

    Returns a dict mapping interaction type -> {"passed": int, "total": int}
    or None if no interactions exist.
    """
    interactions_path = video_dir / "interactions.json"
    if not interactions_path.exists():
        return None

    try:
        interactions_data = json.loads(interactions_path.read_text(encoding="utf-8"))
        interactions = interactions_data.get("interactions", []) if isinstance(interactions_data, dict) else []
    except (json.JSONDecodeError, OSError):
        return None

    review = None
    review_path = video_dir / "review_result.json"
    if review_path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            review = json.loads(review_path.read_text(encoding="utf-8"))

    review_map: dict[str, str] = {}
    global_status: str | None = None
    if isinstance(review, dict):
        global_status = review.get("status")
        for entry in review.get("reviews", []):
            if isinstance(entry, dict) and entry.get("item_id"):
                review_map[str(entry["item_id"])] = str(entry.get("status", ""))

    stats: dict[str, dict[str, int]] = {}
    for node in interactions:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", "unknown"))
        node_id = str(node.get("id", ""))

        if node_type not in stats:
            stats[node_type] = {"passed": 0, "total": 0}

        stats[node_type]["total"] += 1

        node_status = review_map.get(node_id) if node_id else None
        status = node_status if node_status else global_status
        if status == "published":
            stats[node_type]["passed"] += 1

    return stats if stats else None
