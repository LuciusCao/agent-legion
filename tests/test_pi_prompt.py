from server.app.workflows.pi_prompt import build_pi_prompt


def test_prompt_forbids_writing_outputs_into_run_directory() -> None:
    prompt = build_pi_prompt(
        job_id="job-1",
        node_key="subtitle_review",
        job_dir="/data/jobs/job-1",
        skill_dir="/skills/video_knowledge/review_subtitles",
        validator_script="/skills/video_knowledge/review_subtitles/scripts/validate_output.py",
        inputs=["subtitles.srt"],
        outputs=["subtitles_reviewed.srt"],
    )

    assert "runs/" in prompt
    assert "working directory" in prompt.lower()
