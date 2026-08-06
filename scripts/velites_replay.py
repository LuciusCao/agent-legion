#!/usr/bin/env python3
"""velites gray-rollout Phase 1 shadow replay (docs/architecture/velites-harness.md §9).

Samples recent workflow-node runs from a production ``data/jobs`` tree, replays
each prompt/skill snapshot offline through BOTH Node Pi and velites in isolated
temp directories, then diffs the event streams and declared outputs. Production
data is only ever read; nothing touches the database.

Run-dir layouts (``worker/execution_prepare.py`` / ``pi_runner.py``): worker runs
keep only ``<job_dir>/runs/<node_key>/worker/events.jsonl`` (prompt recovered from
the first user message's ``<file ...prompt.md>`` attachment); local runs have
``runs/<node_key>/<run_token>/prompt.md``. Skills resolve from ``--skill-root``
by node-key leaf name, mirroring ``config/workflow.yaml``.

Default mode is dry-run (list samples only); pass ``--execute`` to replay.
velites emits a subset of pi's events (no user/toolResult messages — design §4);
the diff judges structure, never message granularity or usage numbers.

Usage:
    uv run python scripts/velites_replay.py /path/to/data/jobs --limit 5
    uv run python scripts/velites_replay.py /path/to/data/jobs \
        --node review_subtitles --execute --jobs 2 --report replay_report.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROMPT_INSTRUCTION = "Execute the attached node instructions."
PROMPT_ATTACHMENT_RE = re.compile(
    r'<file name="[^"]*prompt\.md">\n(?P<prompt>.*?)\n</file>', re.DOTALL
)
DEFAULT_TOOLS = "read,write,bash"
DEFAULT_MODEL = "kimi-k2.6"


@dataclass(frozen=True)
class Sample:
    job_id: str
    node_key: str
    run_dir: Path
    job_dir: Path
    prompt: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    mtime: float
    prompt_source: str  # "prompt.md" | "events.jsonl"
    skill_dir: Path | None = None


@dataclass(frozen=True)
class HarnessResult:
    harness: str
    exit_code: int
    duration_s: float
    rss_kb_delta: int
    error: str = ""


@dataclass
class SampleReport:
    sample: Sample
    status: str = "dry-run"  # dry-run | skipped | replayed
    reason: str = ""
    pi: dict[str, Any] = field(default_factory=dict)
    velites: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- sampling


def _parse_prompt_sections(prompt: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract 'Declared inputs' / 'Required outputs' bullet lists from a prompt."""

    def bullets(section: str) -> tuple[str, ...]:
        m = re.search(rf"{section}:\n(?P<body>(?:- .*\n)+)", prompt)
        if not m:
            return ()
        return tuple(line[2:].strip() for line in m.group("body").splitlines())

    return bullets("Declared inputs"), bullets("Required outputs")


def _prompt_from_events(events_file: Path) -> str | None:
    """Recover the prompt embedded in the first user message of an events stream."""
    with events_file.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message_start":
                continue
            message = event.get("message") or {}
            if message.get("role") != "user":
                continue
            for part in message.get("content") or []:
                text = str(part.get("text", "")) if isinstance(part, dict) else ""
                m = PROMPT_ATTACHMENT_RE.search(text)
                if m:
                    return m.group("prompt") + "\n"
            return None
    return None


def _load_sample(run_dir: Path, skill_root: Path) -> Sample | None:
    node_key = run_dir.parent.name
    job_dir = run_dir.parent.parent.parent
    prompt_file = run_dir / "prompt.md"
    events_file = run_dir / "events.jsonl"
    prompt: str | None = None
    prompt_source = ""
    if prompt_file.is_file():
        prompt = prompt_file.read_text(encoding="utf-8")
        prompt_source = "prompt.md"
    elif events_file.is_file():
        prompt = _prompt_from_events(events_file)
        prompt_source = "events.jsonl"
    if not prompt:
        return None
    inputs, outputs = _parse_prompt_sections(prompt)
    if not outputs:
        return None
    skill_dir = _resolve_skill(skill_root, node_key)
    return Sample(
        job_id=job_dir.name,
        node_key=node_key,
        run_dir=run_dir,
        job_dir=job_dir,
        prompt=prompt,
        inputs=inputs,
        outputs=outputs,
        mtime=max((p.stat().st_mtime for p in run_dir.iterdir() if p.is_file()), default=0.0),
        prompt_source=prompt_source,
        skill_dir=skill_dir,
    )


def _resolve_skill(skill_root: Path, node_key: str) -> Path | None:
    """Find ``<skill_root>/<domain>/<node_key>/`` with a SKILL.md (leaf name match)."""
    if not skill_root.is_dir():
        return None
    for domain in sorted(skill_root.iterdir()):
        candidate = domain / node_key
        if domain.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate
    return None


def find_samples(
    jobs_dir: Path,
    *,
    skill_root: Path,
    nodes: tuple[str, ...] = (),
    limit: int = 10,
) -> list[Sample]:
    """Newest-first node-run samples under ``<job>/runs/<node>/<token>/``."""
    samples: list[Sample] = []
    for events_file in sorted(jobs_dir.rglob("events.jsonl")):
        run_dir = events_file.parent
        if run_dir.parent.parent.name != "runs":
            continue
        node_key = run_dir.parent.name
        if nodes and node_key not in nodes:
            continue
        sample = _load_sample(run_dir, skill_root)
        if sample is not None:
            samples.append(sample)
    # Runs without events.jsonl never executed (no prompt recovery possible).
    samples.sort(key=lambda s: s.mtime, reverse=True)
    return samples[:limit]


# ---------------------------------------------------------------- replay


def _harness_commands(
    args: argparse.Namespace,
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
) -> dict[str, list[str]]:
    """M2 dual-run templates (data/velites-m2, docs/architecture/velites-m2-validation.md)."""
    session = ["--mode", "json", "--session-dir", str(session_dir), "--name", session_name]
    skill = ["--skill", str(skill_dir), "--tools", DEFAULT_TOOLS]
    model = ["--model", args.model, "--thinking", args.thinking]
    tail = [f"@{prompt_file}", PROMPT_INSTRUCTION]
    pi = [
        args.pi_binary,
        *session,
        "--no-context-files",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-skills",
        *skill,
        "--approve",
        "--provider",
        args.pi_provider,
        *model,
        *tail,
    ]
    velites = [
        args.velites_binary,
        *session,
        *skill,
        "--provider",
        args.velites_provider,
        *model,
        *tail,
    ]
    return {"pi": pi, "velites": velites}


# ru_maxrss is bytes on macOS, KiB on Linux.
_RSS_DIVISOR = 1024 if sys.platform == "darwin" else 1


def _rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // _RSS_DIVISOR


def _run_harness(
    harness: str,
    command: list[str],
    *,
    cwd: Path,
    run_dir: Path,
    timeout: int,
) -> HarnessResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    events_file = run_dir / "events.jsonl"
    stderr_file = run_dir / "stderr.log"
    start = time.monotonic()
    rss_before = _rss_kb()
    error = ""
    try:
        with events_file.open("w") as out, stderr_file.open("w") as err:
            proc = subprocess.run(command, cwd=cwd, stdout=out, stderr=err, timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        exit_code, error = -1, f"timed out after {timeout}s"
    except FileNotFoundError:
        exit_code, error = 127, f"binary not found: {command[0]}"
    return HarnessResult(
        harness=harness,
        exit_code=exit_code,
        duration_s=round(time.monotonic() - start, 1),
        rss_kb_delta=max(0, _rss_kb() - rss_before),
        error=error,
    )


def _prepare_replay_job(sample: Sample, work_dir: Path) -> Path:
    """Stage an isolated replay job dir with the declared inputs copied in."""
    job_dir = work_dir / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    for name in sample.inputs:
        shutil.copy2(sample.job_dir / name, job_dir / name)
    return job_dir


def _render_prompt(sample: Sample, job_dir: Path, skill_dir: Path) -> str:
    # Worker-side prompts keep the manifest placeholders literal; substitute the
    # replay-local paths so the model sees a coherent environment.
    return sample.prompt.replace("{job_dir}", str(job_dir)).replace("{skill_dir}", str(skill_dir))


# ---------------------------------------------------------------- diff


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def _last_assistant_message_end(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        message = event.get("message") or {}
        if event.get("type") == "message_end" and message.get("role") == "assistant":
            return dict(message)
    return {}


def summarize_stream(events: list[dict[str, Any]]) -> dict[str, Any]:
    """stopReason / usage / errorMessage of the final assistant message + tool errors."""
    message = _last_assistant_message_end(events)
    usage = message.get("usage") or {}
    tool_ends = [e for e in events if e.get("type") == "tool_execution_end"]
    return {
        "stop_reason": message.get("stopReason"),
        "error_message": message.get("errorMessage"),
        "usage": {k: usage.get(k) for k in ("input", "output", "cacheRead")},
        "tool_calls": len(tool_ends),
        "tool_errors": sum(1 for e in tool_ends if e.get("isError")),
    }


def _structural_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-side structural integrity: agent boundaries + turn/tool pairing."""
    counts = Counter(str(e.get("type")) for e in events)
    open_calls: dict[str, Any] = {}
    dangling = 0
    for event in events:
        if event.get("type") == "tool_execution_start":
            open_calls[str(event.get("toolCallId"))] = event.get("toolName")
        elif event.get("type") == "tool_execution_end" and (
            open_calls.pop(str(event.get("toolCallId")), None) != event.get("toolName")
        ):
            dangling += 1
    return {
        "agent_start": bool(counts["agent_start"]),
        "agent_end": bool(counts["agent_end"]),
        "turns": counts["turn_start"],
        "turns_paired": counts["turn_start"] == counts["turn_end"],
        "tools_paired": not open_calls and not dangling,
        "tool_calls": counts["tool_execution_end"],
    }


def diff_event_streams(
    pi_events: list[dict[str, Any]], velites_events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Structural checks (judged) + message granularity (informational)."""
    sp, sv = _structural_summary(pi_events), _structural_summary(velites_events)
    pi, velites = summarize_stream(pi_events), summarize_stream(velites_events)
    checks = {
        "agent_boundaries": all(s["agent_start"] and s["agent_end"] for s in (sp, sv)),
        "turns_paired": sp["turns_paired"] and sv["turns_paired"],
        "tools_paired": sp["tools_paired"] and sv["tools_paired"],
        "tool_counts_match": sp["tool_calls"] == sv["tool_calls"],
        "stop_reason_equivalent": pi["stop_reason"] is not None
        and pi["stop_reason"] == velites["stop_reason"],
    }
    msgs_pi = sum(1 for e in pi_events if e.get("type") == "message_end")
    msgs_velites = sum(1 for e in velites_events if e.get("type") == "message_end")
    note = "message event counts match"
    if msgs_pi != msgs_velites:
        note = f"pi emitted {msgs_pi - msgs_velites} extra message events (toolResult/user)"
    return {
        "structural_match": all(checks.values()),
        "checks": checks,
        "structural_pi": sp,
        "structural_velites": sv,
        "message_events_pi": msgs_pi,
        "message_events_velites": msgs_velites,
        "message_note": note,
        "pi": pi,
        "velites": velites,
    }


def _line_similarity(a: Path, b: Path) -> float | None:
    try:
        a_lines = a.read_text(encoding="utf-8").splitlines()
        b_lines = b.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return round(difflib.SequenceMatcher(None, a_lines, b_lines).ratio(), 4)


def diff_outputs(
    outputs: tuple[str, ...], pi_job_dir: Path, velites_job_dir: Path
) -> list[dict[str, Any]]:
    """Existence on both sides plus line-level similarity (model text is not verbatim)."""
    results: list[dict[str, Any]] = []
    for name in outputs:
        pi_file = pi_job_dir / name
        velites_file = velites_job_dir / name
        exists_pi, exists_velites = pi_file.is_file(), velites_file.is_file()
        similarity = (
            _line_similarity(pi_file, velites_file) if exists_pi and exists_velites else None
        )
        results.append(
            {
                "name": name,
                "exists_pi": exists_pi,
                "exists_velites": exists_velites,
                "similarity": similarity,
            }
        )
    return results


# ---------------------------------------------------------------- orchestration


def replay_sample(sample: Sample, args: argparse.Namespace, work_root: Path) -> SampleReport:
    report = SampleReport(sample=sample)
    if sample.skill_dir is None:
        report.status, report.reason = "skipped", f"skill not found under {args.skill_root}"
        return report
    missing = [name for name in sample.inputs if not (sample.job_dir / name).is_file()]
    if missing:
        report.status, report.reason = "skipped", f"missing inputs: {', '.join(missing)}"
        return report
    work_dir = work_root / f"{sample.job_id}__{sample.node_key}"
    results: dict[str, HarnessResult] = {}
    job_dirs: dict[str, Path] = {}
    for harness in ("pi", "velites"):
        job_dir = _prepare_replay_job(sample, work_dir / harness)
        job_dirs[harness] = job_dir
        run_dir = job_dir / "runs" / sample.node_key / "replay"
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = run_dir / "prompt.md"
        prompt_file.write_text(_render_prompt(sample, job_dir, sample.skill_dir), encoding="utf-8")
        commands = _harness_commands(
            args,
            skill_dir=sample.skill_dir,
            session_dir=run_dir / "session",
            session_name=f"replay:{sample.job_id}:{sample.node_key}:{harness}",
            prompt_file=prompt_file,
        )
        results[harness] = _run_harness(
            harness, commands[harness], cwd=job_dir, run_dir=run_dir, timeout=args.timeout
        )
    events_by_harness = {
        harness: load_events(job_dirs[harness] / "runs" / sample.node_key / "replay/events.jsonl")
        for harness in ("pi", "velites")
    }
    for harness, result in results.items():
        getattr(report, harness).update(
            {
                "exit_code": result.exit_code,
                "duration_s": result.duration_s,
                "rss_kb_delta": result.rss_kb_delta,
                "error": result.error,
                **summarize_stream(events_by_harness[harness]),
            }
        )
    report.diff = {
        "events": diff_event_streams(events_by_harness["pi"], events_by_harness["velites"]),
        "outputs": diff_outputs(sample.outputs, job_dirs["pi"], job_dirs["velites"]),
    }
    report.status = "replayed"
    return report


def _report_ok(report: SampleReport) -> bool:
    if report.status != "replayed":
        return True  # dry-run / skipped carry no verdict
    if report.pi.get("exit_code") != 0 or report.velites.get("exit_code") != 0:
        return False
    if not report.diff.get("events", {}).get("structural_match", False):
        return False
    return all(o["exists_pi"] and o["exists_velites"] for o in report.diff.get("outputs", []))


def _print_summary(reports: list[SampleReport], report_path: Path | None) -> None:
    for r in reports:
        s = r.sample
        label = f"[{r.status}] {s.job_id} / {s.node_key} (prompt: {s.prompt_source})"
        print(f"{label} — {r.reason}" if r.reason else label)
        if r.status != "replayed":
            continue
        events = r.diff["events"]
        for name in ("pi", "velites"):
            d = r.pi if name == "pi" else r.velites
            print(
                f"  {name}: exit={d['exit_code']} stop={d['stop_reason']} usage={d['usage']} "
                f"tool_errors={d['tool_errors']} {d['duration_s']}s rssΔ={d['rss_kb_delta']}KB"
            )
        print(f"  structural match: {events['structural_match']}")
        if not events["structural_match"]:
            failed = [k for k, ok in events["checks"].items() if not ok]
            print(f"    failed checks: {failed}")
        print(
            f"  messages: pi={events['message_events_pi']} "
            f"velites={events['message_events_velites']} — {events['message_note']}"
        )
        for o in r.diff["outputs"]:
            sim = f" similarity={o['similarity']}" if o["similarity"] is not None else ""
            print(f"  output {o['name']}: pi={o['exists_pi']} velites={o['exists_velites']}{sim}")
    replayed = [r for r in reports if r.status == "replayed"]
    ok = sum(1 for r in replayed if _report_ok(r))
    print(
        f"\n{len(reports)} sample(s): {len(replayed)} replayed ({ok} consistent), "
        f"{sum(1 for r in reports if r.status == 'skipped')} skipped, "
        f"{sum(1 for r in reports if r.status == 'dry-run')} dry-run"
    )
    if report_path is not None:
        print(f"report: {report_path}")


def _serialize(reports: list[SampleReport]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in reports:
        s = r.sample
        out.append(
            {
                "job_id": s.job_id,
                "node_key": s.node_key,
                "run_dir": str(s.run_dir),
                "prompt_source": s.prompt_source,
                "inputs": list(s.inputs),
                "outputs": list(s.outputs),
                "skill_dir": str(s.skill_dir) if s.skill_dir else None,
                "status": r.status,
                "reason": r.reason,
                "pi": r.pi,
                "velites": r.velites,
                "diff": r.diff,
            }
        )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 1 shadow replay: sample recent node runs from a production jobs "
            "tree, dual-run pi vs velites offline, diff event streams and outputs. "
            "Default is dry-run (list samples only)."
        ),
    )
    parser.add_argument("jobs_dir", type=Path, help="production data/jobs directory (read-only)")
    parser.add_argument("--limit", type=int, default=10, help="max samples (newest first)")
    parser.add_argument(
        "--node",
        action="append",
        default=[],
        help="restrict to node key (capability/skill leaf name); repeatable",
    )
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=Path.home() / ".agents/skills/agent-legion",
        help="skill root containing <domain>/<node_key>/ dirs",
    )
    parser.add_argument("--execute", action="store_true", help="actually replay (default dry-run)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pi-provider", default="deepseek")
    parser.add_argument("--velites-provider", default="gateway")
    parser.add_argument("--thinking", default="low")
    parser.add_argument("--pi-binary", default="pi")
    parser.add_argument("--velites-binary", default="velites")
    parser.add_argument("--timeout", type=int, default=900, help="per-harness timeout (s)")
    parser.add_argument("--jobs", type=int, default=1, help="parallel samples")
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="replay staging dir (default: fresh temp dir, kept for inspection)",
    )
    parser.add_argument("--report", type=Path, default=None, help="JSON report output path")
    return parser


def _resolve_binary(value: str) -> str:
    """Resolve path-like binaries to absolute (harnesses run with cwd=replay dir)."""
    if "/" in value:
        return str(Path(value).resolve())
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    jobs_dir = args.jobs_dir.resolve()
    if not jobs_dir.is_dir():
        print(f"jobs_dir not found: {jobs_dir}", file=sys.stderr)
        return 2
    args.pi_binary = _resolve_binary(args.pi_binary)
    args.velites_binary = _resolve_binary(args.velites_binary)
    samples = find_samples(
        jobs_dir, skill_root=args.skill_root, nodes=tuple(args.node), limit=args.limit
    )
    if not samples:
        print("no samples found (need runs/<node>/<token>/ with prompt.md or events.jsonl)")
        return 0

    reports: list[SampleReport] = []
    work_root = args.work_root
    if not args.execute:
        reports = [
            SampleReport(sample=s, reason="dry-run (pass --execute to replay)") for s in samples
        ]
    else:
        if work_root is None:
            work_root = Path(tempfile.mkdtemp(prefix="velites-replay-"))
        work_root.mkdir(parents=True, exist_ok=True)
        print(f"replay work root: {work_root}")
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            reports = list(pool.map(lambda s: replay_sample(s, args, work_root), samples))

    report_path = args.report
    if report_path is None and args.execute:
        report_path = work_root / "replay_report.json"
    if report_path is not None:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "jobs_dir": str(jobs_dir),
            "config": {
                "model": args.model,
                "pi_provider": args.pi_provider,
                "velites_provider": args.velites_provider,
                "thinking": args.thinking,
                "timeout": args.timeout,
                "execute": args.execute,
            },
            "samples": _serialize(reports),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(reports, report_path)
    if not args.execute:
        return 0
    return 0 if all(_report_ok(r) for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
