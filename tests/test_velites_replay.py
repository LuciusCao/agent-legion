from __future__ import annotations

import json
from pathlib import Path

from scripts import velites_replay as vr

PROMPT = """Execute the loaded node skill for this Agent Legion workflow job.

Job ID: job_1
Node: review_subtitles
Working directory: {job_dir}
Skill directory: {skill_dir}
Validator script: {skill_dir}/scripts/validate_output.py

Declared inputs:
- subtitles.srt
- extra.json

Required outputs:
- subtitles_reviewed.srt
- review_report.json

Write required outputs directly into the working directory.
"""

ATTACHED = (
    f'<file name="/prod/job/runs/review_subtitles/worker/prompt.md">\n{PROMPT}</file>\n'
    "Execute the attached node instructions."
)


def _make_run(
    jobs_dir: Path,
    job: str,
    node: str,
    token: str,
    *,
    prompt: str | None = PROMPT,
    with_events: bool = True,
    mtime: float = 0.0,
) -> Path:
    run_dir = jobs_dir / job / "runs" / node / token
    run_dir.mkdir(parents=True)
    if prompt is not None:
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    if with_events:
        events = [
            {"type": "session", "id": "x"},
            {
                "type": "message_start",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": ATTACHED}],
                },
            },
        ]
        events_file = run_dir / "events.jsonl"
        events_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    if mtime:
        import os

        for path in run_dir.iterdir():
            os.utime(path, (mtime, mtime))
    return run_dir


def _make_skill_root(root: Path, node: str, domain: str = "video_knowledge") -> Path:
    skill_dir = root / domain / node
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


# ------------------------------------------------------------- prompt parsing


def test_parse_prompt_sections() -> None:
    inputs, outputs = vr._parse_prompt_sections(PROMPT)
    assert inputs == ("subtitles.srt", "extra.json")
    assert outputs == ("subtitles_reviewed.srt", "review_report.json")


def test_prompt_from_events_attachment(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "s"}),
                json.dumps(
                    {
                        "type": "message_start",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": ATTACHED}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert vr._prompt_from_events(events_file) == PROMPT


def test_prompt_from_events_missing(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps({"type": "agent_start"}), encoding="utf-8")
    assert vr._prompt_from_events(events_file) is None


# ------------------------------------------------------------------ sampling


def test_find_samples_newest_first_and_node_filter(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    _make_skill_root(skill_root, "review_subtitles")
    _make_skill_root(skill_root, "generate_chapters")
    old = _make_run(jobs, "job_a", "review_subtitles", "worker", mtime=100.0)
    _make_run(jobs, "job_b", "generate_chapters", "worker", mtime=200.0)
    new = _make_run(jobs, "job_c", "review_subtitles", "worker", mtime=300.0)

    samples = vr.find_samples(jobs, skill_root=skill_root)
    assert [s.run_dir for s in samples] == [new, samples[1].run_dir, old]
    assert samples[1].node_key == "generate_chapters"
    assert samples[0].node_key == "review_subtitles"

    filtered = vr.find_samples(jobs, skill_root=skill_root, nodes=("generate_chapters",))
    assert len(filtered) == 1
    assert filtered[0].node_key == "generate_chapters"

    limited = vr.find_samples(jobs, skill_root=skill_root, limit=1)
    assert len(limited) == 1
    assert limited[0].run_dir == new


def test_find_samples_recovers_prompt_from_events_only(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    _make_skill_root(skill_root, "review_subtitles")
    _make_run(jobs, "job_w", "review_subtitles", "worker", prompt=None)

    samples = vr.find_samples(jobs, skill_root=skill_root)
    assert len(samples) == 1
    assert samples[0].prompt_source == "events.jsonl"
    assert samples[0].outputs == ("subtitles_reviewed.srt", "review_report.json")
    assert samples[0].skill_dir == skill_root / "video_knowledge" / "review_subtitles"


def test_find_samples_prefers_prompt_md(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    _make_skill_root(skill_root, "review_subtitles")
    _make_run(jobs, "job_l", "review_subtitles", "uuid-token")

    samples = vr.find_samples(jobs, skill_root=skill_root)
    assert len(samples) == 1
    assert samples[0].prompt_source == "prompt.md"


def test_find_samples_skips_run_without_prompt_and_unresolved_skill(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    _make_skill_root(skill_root, "review_subtitles")
    # events without an embedded prompt -> not a sample
    run_dir = jobs / "job_x" / "runs" / "review_subtitles" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text('{"type": "agent_start"}', encoding="utf-8")
    # valid prompt but unknown skill -> sample kept, skill_dir None
    _make_run(jobs, "job_y", "unknown_node", "worker")

    samples = vr.find_samples(jobs, skill_root=skill_root)
    assert len(samples) == 1
    assert samples[0].node_key == "unknown_node"
    assert samples[0].skill_dir is None


def test_render_prompt_substitutes_placeholders(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    skill_dir = _make_skill_root(skill_root, "review_subtitles")
    _make_run(jobs, "job_r", "review_subtitles", "worker")
    sample = vr.find_samples(jobs, skill_root=skill_root)[0]
    job_dir = tmp_path / "replay" / "job"
    rendered = vr._render_prompt(sample, job_dir, skill_dir)
    assert f"Working directory: {job_dir}" in rendered
    assert f"Skill directory: {skill_dir}" in rendered
    assert "{job_dir}" not in rendered


# ---------------------------------------------------------------- event diff


def _assistant_end(stop: str = "stop", error: str | None = None) -> dict:
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "done"}],
        "usage": {"input": 10, "output": 5, "cacheRead": 2},
        "stopReason": stop,
    }
    if error:
        message["errorMessage"] = error
    return {"type": "message_end", "message": message}


def _stream(*, stop: str = "stop", tool_ends: int = 1, extra_messages: bool = False) -> list[dict]:
    """A minimal well-formed agent run: boundaries, one turn, one tool pair."""
    events: list[dict] = [{"type": "session"}, {"type": "agent_start"}]
    if extra_messages:
        events.append({"type": "message_end", "message": {"role": "user"}})
    events.append({"type": "turn_start"})
    for i in range(tool_ends):
        events.append({"type": "tool_execution_start", "toolCallId": f"t{i}", "toolName": "bash"})
        events.append(
            {
                "type": "tool_execution_end",
                "toolCallId": f"t{i}",
                "toolName": "bash",
                "isError": False,
            }
        )
        if extra_messages:
            events.append({"type": "message_end", "message": {"role": "toolResult"}})
    events.append(_assistant_end(stop=stop))
    events += [{"type": "turn_end"}, {"type": "agent_end"}]
    return events


def _report(pi_events: list[dict], ve_events: list[dict], *, outputs_exist: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(
        status="replayed",
        pi={"exit_code": 0},
        velites={"exit_code": 0},
        diff={
            "events": vr.diff_event_streams(pi_events, ve_events),
            "outputs": [
                {
                    "name": "out.json",
                    "exists_pi": outputs_exist,
                    "exists_velites": outputs_exist,
                    "similarity": 1.0,
                }
            ],
        },
    )


def test_extra_tool_result_and_user_messages_still_consistent() -> None:
    # pi additionally emits user/toolResult messages (velites: subset by
    # design §4); structure identical -> consistent, difference noted only.
    pi = _stream(extra_messages=True)
    velites = _stream()
    diff = vr.diff_event_streams(pi, velites)
    assert diff["structural_match"] is True
    assert diff["message_events_pi"] > diff["message_events_velites"]
    assert "extra message events" in diff["message_note"]
    assert vr._report_ok(_report(pi, velites)) is True


def test_tool_execution_boundary_mismatch_not_consistent() -> None:
    pi = _stream(tool_ends=2)
    velites = _stream(tool_ends=1)  # missing one tool_execution pair
    diff = vr.diff_event_streams(pi, velites)
    assert diff["checks"]["tool_counts_match"] is False
    assert diff["structural_match"] is False
    assert vr._report_ok(_report(pi, velites)) is False

    dangling = _stream(tool_ends=1)[:-2]  # drop turn_end/agent_end -> unpaired
    dangling.append({"type": "turn_end"})
    dangling.append({"type": "agent_end"})
    dangling.insert(-3, {"type": "tool_execution_start", "toolCallId": "x", "toolName": "bash"})
    diff = vr.diff_event_streams(_stream(tool_ends=1), dangling)
    assert diff["checks"]["tools_paired"] is False
    assert diff["structural_match"] is False


def test_stop_reason_mismatch_not_consistent() -> None:
    diff = vr.diff_event_streams(_stream(stop="stop"), _stream(stop="error"))
    assert diff["checks"]["stop_reason_equivalent"] is False
    assert diff["structural_match"] is False
    assert vr._report_ok(_report(_stream(stop="stop"), _stream(stop="error"))) is False

    both_error = vr.diff_event_streams(_stream(stop="error"), _stream(stop="error"))
    assert both_error["checks"]["stop_reason_equivalent"] is True


def test_usage_and_tool_errors_recorded_not_judged() -> None:
    diff = vr.diff_event_streams(_stream(), _stream())
    assert diff["pi"]["usage"] == {"input": 10, "output": 5, "cacheRead": 2}
    assert diff["pi"]["tool_calls"] == 1
    assert diff["pi"]["tool_errors"] == 0
    assert diff["pi"]["stop_reason"] == "stop"


def test_summarize_stream_uses_last_assistant_message_end() -> None:
    events = [
        _assistant_end(stop="toolUse"),
        _assistant_end(stop="stop", error="boom"),
    ]
    summary = vr.summarize_stream(events)
    assert summary["stop_reason"] == "stop"
    assert summary["error_message"] == "boom"


# --------------------------------------------------------------- output diff


def test_diff_outputs_existence_and_similarity(tmp_path: Path) -> None:
    pi_dir = tmp_path / "pi"
    velites_dir = tmp_path / "velites"
    pi_dir.mkdir()
    velites_dir.mkdir()
    (pi_dir / "a.json").write_text('{\n  "x": 1\n}\n', encoding="utf-8")
    (velites_dir / "a.json").write_text('{\n  "x": 1\n}\n', encoding="utf-8")
    (pi_dir / "b.json").write_text("only pi\n", encoding="utf-8")

    results = vr.diff_outputs(("a.json", "b.json", "c.json"), pi_dir, velites_dir)
    by_name = {r["name"]: r for r in results}
    assert by_name["a.json"]["exists_pi"] and by_name["a.json"]["exists_velites"]
    assert by_name["a.json"]["similarity"] == 1.0
    assert by_name["b.json"]["exists_pi"] and not by_name["b.json"]["exists_velites"]
    assert by_name["b.json"]["similarity"] is None
    assert not by_name["c.json"]["exists_pi"] and not by_name["c.json"]["exists_velites"]


# ------------------------------------------------------------- dry-run CLI


def test_main_dry_run_lists_samples(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    skill_root = tmp_path / "skills"
    _make_skill_root(skill_root, "review_subtitles")
    _make_run(jobs, "job_d", "review_subtitles", "worker")

    exit_code = vr.main([str(jobs), "--skill-root", str(skill_root)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[dry-run] job_d / review_subtitles" in out
    assert "1 dry-run" in out
