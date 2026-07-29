"""Assemble upload_params.json in llm_claude format.

This module mirrors the data-transformation logic from
llm_claude/Step3_upload_results.py so that agent-legion packages produce
compatible output.
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from server.app.pipeline.common import parse_srt


def _new_uuid32() -> str:
    return uuid.uuid4().hex


def _to_uuid32(value: str) -> str:
    if not value:
        return _new_uuid32()
    cleaned = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(cleaned) >= 32:
        return cleaned[:32].lower()
    return (cleaned + _new_uuid32())[:32].lower()


def _clean_subtitle_text(text: str) -> str:
    STRIP_PATTERNS = ["```", "\n"]
    changed = True
    while changed:
        changed = False
        for pat in STRIP_PATTERNS:
            if text.startswith(pat):
                text = text[len(pat) :]
                changed = True
            if text.endswith(pat):
                text = text[: -len(pat)]
                changed = True
    return text


def build_subtitles(srt_text: str) -> list[dict]:
    subs = parse_srt(srt_text)
    out = []
    for i, s in enumerate(subs, 1):
        text = _clean_subtitle_text(s["text"])
        if not text:
            continue
        out.append(
            {
                "sequence": i,
                "start_time": int(round(s["start"] * 1000)),
                "end_time": int(round(s["end"] * 1000)),
                "text": text,
            }
        )
    return out


def build_clips(chapters: list[dict]) -> list[dict]:
    out = []
    for c in chapters:
        start_raw = c.get("start_time") if "start_time" in c else c.get("start", 0)
        end_raw = c.get("end_time") if "end_time" in c else c.get("end", 0)
        start = start_raw if start_raw is not None else 0
        end = end_raw if end_raw is not None else 0
        out.append(
            {
                "clips_uuid": _new_uuid32(),
                "start_time": int(round(float(start) * 1000)),
                "end_time": int(round(float(end) * 1000)),
                "title": c.get("title", ""),
            }
        )
    return out


def _map_review_status(status: str) -> int:
    return {"published": 3, "pending_review": 2, "rejected": 2}.get(status, 1)


def _build_review_msg(issues: list) -> str:
    msgs = []
    for issue in issues:
        title = issue.get("title", "")
        details = issue.get("details", "")
        if title and details:
            msgs.append(f"{title}：{details}")
        elif details:
            msgs.append(details)
        elif title:
            msgs.append(title)
    return "；".join(msgs)


def _checklist_reviews(checklist_data: dict) -> list[dict]:
    issues_by_node: dict[str, list] = {}
    checklist = checklist_data.get("checklist", {})
    if not isinstance(checklist, dict):
        return []
    for dimension in checklist.values():
        if not isinstance(dimension, dict):
            continue
        for issue in dimension.get("issues", []):
            if not isinstance(issue, dict):
                continue
            node_id = issue.get("node_id", "")
            if node_id:
                issues_by_node.setdefault(node_id, []).append(issue)
    return [
        {"item_id": node_id, "status": "pending_review", "issues": issues}
        for node_id, issues in issues_by_node.items()
    ]


def split_interactions(
    interactions: list[dict], reviews: list[dict] | None = None
) -> tuple[list[dict], list[dict]]:
    review_map = {}
    if reviews:
        for r in reviews:
            review_map[r.get("item_id", "")] = r

    trials = []
    summaries = []
    for inter in interactions:
        itype = inter.get("type")
        trigger_ms = int(round(float(inter.get("trigger_time", 0)) * 1000))
        iuuid = _to_uuid32(inter.get("id", ""))
        raw_id = inter.get("id", "")

        review = review_map.get(raw_id, {})
        r_status = _map_review_status(review.get("status", "pending_review"))
        r_msg = "" if r_status == 3 else _build_review_msg(review.get("issues", []))

        if itype == "example_practice":
            trials.append(
                {
                    "example_problem_trial_uuid": iuuid,
                    "start_time": trigger_ms,
                    "instruction": inter.get("instruction", ""),
                    "hint": inter.get("hint", ""),
                    "review_status": r_status,
                    "review_msg": r_msg,
                    "is_deleted": 0,
                }
            )
        elif itype == "video_summary":
            raw_options = inter.get("options", []) or []
            raw_answer = inter.get("answer", []) or []

            key_map = {}
            options = []
            for idx, opt in enumerate(raw_options):
                key = chr(ord("A") + idx)
                opt_id = opt.get("id", f"opt_{idx}")
                key_map[opt_id] = key
                options.append(
                    {
                        "interaction_summary_options_uuid": opt.get("id", ""),
                        "key": key,
                        "content": opt.get("text", "").replace("\n", "。"),
                        "is_distractor": bool(opt.get("is_distractor", False)),
                    }
                )

            answer = [key_map.get(a, a) for a in raw_answer]

            summaries.append(
                {
                    "interaction_summary_uuid": iuuid,
                    "type": "video_summary",
                    "start_time": trigger_ms,
                    "instruction": inter.get("instruction", ""),
                    "reference_sentence": inter.get("reference_sentence", ""),
                    "options": options,
                    "answer": answer,
                    "grading_mode": inter.get("grading_mode", "strict_sequence"),
                    "review_status": r_status,
                    "review_msg": r_msg,
                    "is_deleted": 0,
                }
            )
    return trials, summaries


def build_upload_params(video: dict, video_dir: Path) -> dict:
    """Assemble upload_params.json content compatible with llm_claude format."""
    srt_path = video_dir / "subtitles_reviewed.srt"
    if not srt_path.exists():
        srt_path = video_dir / "subtitles.srt"
    chapters_path = video_dir / "chapters.json"
    interactions_path = video_dir / "interactions.json"

    subtitles = []
    if srt_path.exists():
        subtitles = build_subtitles(srt_path.read_text(encoding="utf-8"))

    clips = []
    if chapters_path.exists():
        chapters_raw = json.loads(chapters_path.read_text(encoding="utf-8"))
        if isinstance(chapters_raw, list):
            clips = build_clips(chapters_raw)
        elif isinstance(chapters_raw, dict):
            clips = build_clips(chapters_raw.get("chapters", []))

    interactions: list[Any] = []
    if interactions_path.exists():
        inter_data = json.loads(interactions_path.read_text(encoding="utf-8"))
        if isinstance(inter_data, dict):
            interactions = inter_data.get("interactions") or inter_data.get("nodes") or []
        elif isinstance(inter_data, list):
            interactions = inter_data

    reviews = []
    default_review_status = "pending_review"
    review_result_path = video_dir / "review_result.json"
    if review_result_path.exists():
        try:
            review_data = json.loads(review_result_path.read_text(encoding="utf-8"))
            default_review_status = review_data.get("status", default_review_status)
            reviews = review_data.get("reviews", [])
        except (json.JSONDecodeError, ValueError):
            pass
    checklist_path = video_dir / "checklist.json"
    if checklist_path.exists():
        try:
            checklist_data = json.loads(checklist_path.read_text(encoding="utf-8"))
            reviews.extend(_checklist_reviews(checklist_data))
        except (json.JSONDecodeError, ValueError):
            pass

    reviewed_ids = {review.get("item_id", "") for review in reviews}
    for inter in interactions:
        inter_id = inter.get("id", "")
        if inter_id and inter_id not in reviewed_ids:
            reviews.append({"item_id": inter_id, "status": default_review_status, "issues": []})

    trials, summaries = split_interactions(interactions, reviews)

    return {
        "source_uuid": video.get("source_uuid", ""),
        "subtitles_json": subtitles,
        "subtitles_review_status": "3",
        "clips_json": clips,
        "clips_review_status": "3",
        "example_problem_trial_json": trials,
        "interaction_summary_json": summaries,
    }


def write_upload_params(video: dict, video_dir: Path) -> Path:
    params = build_upload_params(video, video_dir)
    path = video_dir / "upload_params.json"
    path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
