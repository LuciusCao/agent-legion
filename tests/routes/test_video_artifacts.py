import json


def test_artifacts_endpoint_includes_checklist_and_review(tmp_path, client):

    created = client.post(
        "/api/videos",
        json={
            "items": [
                {
                    "url": "https://example.com/course/v1.mp4",
                    "title": "V1",
                    "content_type": "knowledge",
                    "external_id": "V001",
                }
            ]
        },
    )
    assert created.status_code == 200
    video_id = created.json()["videos"][0]["id"]

    video_dir = tmp_path / "videos" / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "subtitles.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
    )
    (video_dir / "interactions.json").write_text(json.dumps({"interactions": []}), encoding="utf-8")
    (video_dir / "chapters.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (video_dir / "checklist.json").write_text(
        json.dumps({"video_id": video_id, "checklist": {"content_usability": {"issues": []}}}),
        encoding="utf-8",
    )
    (video_dir / "review_result.json").write_text(
        json.dumps({"score": 95, "status": "published"}), encoding="utf-8"
    )

    artifacts = client.get(f"/api/videos/{video_id}/artifacts").json()
    assert artifacts["checklist"] is not None
    assert artifacts["checklist"]["checklist"]["content_usability"]["issues"] == []
    assert artifacts["review"] is not None
    assert artifacts["review"]["score"] == 95
