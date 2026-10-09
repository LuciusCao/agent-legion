"""Worker state-dir evidence dumps for unattributable execution failures
(issue #1147).

Two incident families dump forensic evidence into ``<state_dir>/evidence/``
— deliberately OUTSIDE the work root, so the evidence survives the very
deletion it is meant to explain:

1. **prep failures** — ``worker/upload/prepare.py``'s degradation branch
   (``prepare_or_failed``). A prepare pass that raises used to tear the
   execution dir down with zero trace; the #1147 signature is an agent
   that deleted its own run directory late in an otherwise successful run
   (exit 0), so ``tar.add(run_dir)`` fails with FileNotFoundError and
   events.jsonl — the only tool-call record — is gone. The dump captures
   whatever still exists, before the queue cleans the directory.
2. **event-pump write failures** — ``worker/execution/reactor.py``. When
   events.jsonl itself stops accepting writes (run dir deleted mid-run),
   the stream's events are diverted into the emergency dump instead of
   being lost with the directory.

Layout: one incident directory per ``<execution_id>__<node_key>`` — the
pair-scoped name is the issue's 防覆盖 requirement (dumps from different
executions/nodes never overwrite each other; a re-entering prepare or a
re-claimed attempt of the SAME pair rewrites its own incident, the fresh
state being the more accurate one). Files inside:

- ``events.jsonl`` — the (redacted, compressed) events copy, produced by
  the SAME single-pass scan/compress as the delivery path
  (``shared/pi_events.py::scan_and_compress_pi_events``), so the dump
  reuses the same secret-registry snapshot (``secret_snapshot``) and
  redaction functions as the outbound faces (#842/#844): no plaintext
  secret may land in the worker state directory. A file that is already
  gone is recorded absent; an unreadable one (EACCES family — the agent
  chmod'd its own run dir) records an ``unreadable`` note; a scan failure
  deletes the raw copy (fail-closed — an unprocessed copy never stays
  behind).
- ``agent-stderr.log`` — the rescued stderr tail (already redacted), same
  rescue and bound as the delivery path (``stderr_tail_for_run``).
- ``events-emergency.jsonl`` — the pump's diverted stream, redacted per
  line at write time (JSON events keep their structure via
  ``redact_json``, non-JSON lines via span redaction); one unrenderable
  line is dropped in isolation, never the batch, and oversized lines are
  cut with an INLINE marker so one framed line stays one physical line.
- ``listing.txt`` — what still existed under the execution dir at dump
  time; with the run dir gone, this is the last-known directory listing
  (an unwalkable tree records ``listing_failed`` instead).
- ``incident.json`` — the machine-readable record (identity, exit code,
  the redacted prepare error, which parts were absent/unreadable/failed).

Retention: these incidents are rare by construction (an agent must destroy
its own working tree, or the events write path must fail); there is no TTL
sweeper for them — clean up manually after the post-mortem. The evidence
root is ``<state_dir>/evidence``; the executor derives the state dir from
``--config``'s parent (the same source as ``WorkerConfigStore`` /
registration), so no extra wiring exists. Tests configure it via
``configure_evidence_root`` / ``reset_evidence_root``.

Everything here is best-effort by contract: a dump failure logs and
returns None / writes nothing — it must never turn a reportable result
into a lost one, and it never retries on the critical path. Within one
prep dump every arm degrades independently (#1147 review P3-1: an agent
chmod'ing its own run dir is this issue's family) — one unreadable piece
records its note and the remaining arms still land, "capture whatever
still exists" is the contract.
"""

from __future__ import annotations

import errno
import json
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.pi_events import scan_and_compress_pi_events
from shared.stderr_tail import AGENT_STDERR_FILENAME
from worker._atomic import atomic_write
from worker.upload.stderr_evidence import secret_snapshot, stderr_tail_for_run

if TYPE_CHECKING:
    from shared.redaction import SecretRedactor
    from worker.upload.task import UploadTask

EVIDENCE_DIRNAME = "evidence"
# Grep-able error_message marker for the vanished-working-tree family; the
# Host's failure classification maps it to technical/work_dir_missing
# (server/app/services/failure_classification/markers.py keeps the literal).
WORK_DIR_MISSING_MARKER = "[work-dir-missing]"
EMERGENCY_EVENTS_FILENAME = "events-emergency.jsonl"
INCIDENT_RECORD_FILENAME = "incident.json"
LISTING_FILENAME = "listing.txt"
_MAX_LISTING_ENTRIES = 2000
# One dump line after redaction, and one sink's total in-process growth:
# bounded so a pathological agent cannot fill the state volume through the
# emergency path (the delivery-side bound is the pump's stream filter).
_MAX_DUMP_LINE_CHARS = 64 * 1024
_MAX_DUMP_TOTAL_BYTES = 64 * 1024 * 1024
# errno family that means "the working tree is gone" (as opposed to e.g.
# PermissionError, where the tree exists but is unreadable).
_MISSING_TREE_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR})

_evidence_root: Path | None = None


def configure_evidence_root(state_dir: Path) -> Path:
    """Register ``<state_dir>/evidence`` as the dump root (executor startup).

    Set once before any claim exists; the dump sites (upload pool threads,
    reactor parse pool threads) read the module global afterwards."""
    global _evidence_root
    _evidence_root = Path(state_dir) / EVIDENCE_DIRNAME
    return _evidence_root


def reset_evidence_root() -> None:
    """Test hook: disable dumping again (module state is process-wide)."""
    global _evidence_root
    _evidence_root = None


def evidence_root() -> Path | None:
    """The configured dump root, or None when dumping is disabled."""
    return _evidence_root


def _safe_segment(value: str, fallback: str) -> str:
    """One incident-name segment: execution ids and node keys come from the
    wire, so path separators and traversal fodder are neutralized."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("._")
    return (cleaned or fallback)[:64]


def incident_dir(execution_id: str, node_key: str, fallback: str = "") -> Path | None:
    """The pair-scoped incident directory, or None when dumping is disabled.
    ``fallback`` labels an unidentified stream (a reactor registration
    without an execution id) so two such streams never share a directory."""
    if _evidence_root is None:
        return None
    identity = execution_id or fallback
    return (
        _evidence_root
        / f"{_safe_segment(identity, 'execution')}__{_safe_segment(node_key, 'node')}"
    )


def prep_failure_message(task: UploadTask, exc: BaseException, evidence: Path | None) -> str:
    """error_message for a degraded prepare pass.

    The vanished-tree family (``FileNotFoundError``/``NotADirectoryError``
    naming a path inside this execution's dir) gets the
    ``WORK_DIR_MISSING_MARKER`` prefix — grep-able, and distinct from an
    agent that ran but produced nothing (the output_missing family): an
    infrastructure accident vs an execution problem for on-call triage. A
    successful dump appends where the evidence landed."""
    message = f"result preparation failed: {secret_snapshot().redact(str(exc))}"
    if tree_missing_inside(exc, task.execution_dir):
        message = f"{WORK_DIR_MISSING_MARKER} {message}"
    if evidence is not None:
        message = f"{message}; evidence preserved at {evidence}"
    return message


def tree_missing_inside(exc: BaseException, execution_dir: Path) -> bool:
    """True when the failure names a vanished path inside this execution's
    own directory tree — the #1147 signature (the run dir or a component
    of it was deleted, e.g. by the agent itself)."""
    if not isinstance(exc, OSError) or exc.errno not in _MISSING_TREE_ERRNOS:
        return False
    root = execution_dir.resolve()
    for name in (exc.filename, exc.filename2):
        if not name:
            continue
        try:
            if Path(str(name)).resolve().is_relative_to(root):
                return True
        except OSError:
            continue
    return False


def dump_prep_evidence(task: UploadTask, exc: BaseException) -> Path | None:
    """Best-effort forensic dump for one failed prepare pass (#1147).

    Called from ``prepare_or_failed``'s degradation branch BEFORE the queue
    tears the execution dir down. Returns the incident dir for the
    error_message pointer, or None (disabled / the dump itself failed)."""
    target = incident_dir(task.execution_id, task.node_key)
    if target is None:
        return None
    try:
        return _dump_prep_evidence(target, task, exc)
    except Exception as dump_exc:
        # #204 broad-except audit: 转储是纯观测面兜底，失败语义是「本次
        # 不留证据」，绝不允许它把一个本可上报的 failed 结果变成丢失的
        # 结果（吞掉后主降级路径照常写空归档 + failed_metadata）。逃逸族
        # 混族（state 目录不可写、清单遍历 OSError、JSON 序列化）但后果
        # 空间一致且有界。日志保全：print 记录 execution_id 与异常。
        print(f"prep evidence dump failed for {task.execution_id}: {dump_exc!r}", flush=True)
        return None


def _dump_prep_evidence(target: Path, task: UploadTask, exc: BaseException) -> Path:
    """Every arm degrades independently (review P3-1): an unreadable run dir
    (EACCES family) records its per-arm note and the remaining arms still
    land — a chmod'd tree is half the forensic story, not a reason to keep
    an empty incident directory."""
    target.mkdir(parents=True, exist_ok=True)
    redactor = secret_snapshot()
    run_dir = task.execution_dir / "job" / "runs" / task.node_key / "worker"
    events_state, scanned_tail = _dump_events_copy(target, run_dir / "events.jsonl", redactor)
    tail = _dump_stderr_tail(target, run_dir, scanned_tail)
    try:
        dir_present = task.execution_dir.is_dir()
    except OSError:
        dir_present = False
    listing_state = (
        _dump_listing(target, task.execution_dir)
        if dir_present
        else "skipped (execution dir absent)"
    )
    _dump_incident_record(
        target, task, exc, redactor, events_state, listing_state, dir_present, bool(tail)
    )
    return target


def _dump_events_copy(target: Path, events: Path, redactor: SecretRedactor) -> tuple[str, bytes]:
    """Copy events.jsonl through the delivery path's compress pass.

    The copy is redacted and delta-compressed exactly like the archived
    face. Fail-closed on a scan failure: the staging rewrite leaves the
    copy untouched (raw), so the copy is deleted rather than kept — an
    unredacted file never stays in the state directory. An OSError on the
    probe or the copy itself (the source vanished mid-read, or the run dir
    was chmod'd unreadable) degrades to an ``unreadable`` note with the
    errno — distinct from the ENOENT family's plain ``absent``."""
    try:
        present = events.is_file()
    except OSError as probe_exc:
        return f"unreadable: {probe_exc}", b""
    if not present:
        return "absent", b""
    copy = target / "events.jsonl"
    try:
        shutil.copyfile(events, copy)
        size = copy.stat().st_size
        _, original, _, tail = scan_and_compress_pi_events(copy, redactor=redactor)
    except OSError as copy_exc:
        copy.unlink(missing_ok=True)
        return f"unreadable: {copy_exc}", b""
    if size > 0 and original == 0:
        copy.unlink(missing_ok=True)
        return f"scan failed (source was {size} bytes; raw copy discarded)", b""
    return "dumped", tail


def _dump_stderr_tail(target: Path, run_dir: Path, scanned_tail: bytes) -> bytes:
    """The rescued stderr tail, redacted (both arms of the delivery-path
    rescue: the fresh scan's capture, or the scan-time anchor when the
    events file was already compressed on a re-entry). An unreadable run
    dir degrades to no tail — the other arms are unaffected."""
    try:
        tail = stderr_tail_for_run(run_dir, scanned_tail)
        if tail:
            (target / AGENT_STDERR_FILENAME).write_bytes(tail)
        return tail
    except OSError as exc:
        print(f"prep evidence stderr tail unreadable: {exc!r}", flush=True)
        return b""


def _dump_listing(target: Path, execution_dir: Path) -> str:
    """The surviving-tree listing as a record state: ``"<n> entries"`` or
    ``"listing_failed"`` when the walk/write itself raised (EACCES family)
    — ``rglob`` silently skips unreadable subtrees, so a failure here means
    the walk itself died, not that a chmod'd child hid its files."""
    entries: list[str] = []
    try:
        for path in execution_dir.rglob("*"):
            entries.append(path.relative_to(execution_dir).as_posix())
            if len(entries) >= _MAX_LISTING_ENTRIES:
                break
        entries.sort()
        (target / LISTING_FILENAME).write_text(
            "\n".join(entries) + ("\n" if entries else ""), encoding="utf-8"
        )
    except OSError as exc:
        print(f"prep evidence listing failed for {execution_dir}: {exc!r}", flush=True)
        return "listing_failed"
    return f"{len(entries)} entries"


def _dump_incident_record(
    target: Path,
    task: UploadTask,
    exc: BaseException,
    redactor: SecretRedactor,
    events_state: str,
    listing_state: str,
    dir_present: bool,
    stderr_present: bool,
) -> None:
    record = {
        "execution_id": task.execution_id,
        "node_key": task.node_key,
        "lease_id": task.lease_id,
        "exit_code": task.exit_code,
        "error": redactor.redact(str(exc)),
        "events": events_state,
        "execution_dir_present": dir_present,
        "stderr_tail": stderr_present,
        "listing": listing_state,
        "pump_emergency_dump": (target / EMERGENCY_EVENTS_FILENAME).is_file(),
        "dumped_at": time.time(),
    }
    atomic_write(
        target / INCIDENT_RECORD_FILENAME,
        json.dumps(record, ensure_ascii=False, indent=2),
    )


class EmergencyEventsSink:
    """Append-only, per-line-redacted sink for one stream's diverted events
    (#1147 pump write failure).

    Per-batch open-append-close (binary): crash-safe, no handle shared
    across pool tasks. One line's render/redact/ENCODE failure is dropped
    in isolation — never the batch (review P3-2: the encode used to sit
    outside the per-line guard, so a lone-surrogate line killed its whole
    batch; delivery's per-line semantics apply here too). Both one line
    (after redaction) and the sink's total in-process growth are capped,
    so the emergency path cannot fill the state volume. All failures are
    swallowed after logging — the sink is an observation face, never a
    control-flow input."""

    def __init__(self, path: Path, redactor: SecretRedactor) -> None:
        self.path = path
        self._redactor = redactor
        self._written = 0
        self._capped = False

    def write_lines(self, lines: list[bytes]) -> None:
        if self._capped or not lines:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as dst:
                for line in lines:
                    try:
                        payload = _render_line(line, self._redactor).encode("utf-8")
                    except Exception:
                        # #204 broad-except audit: 单行渲染/脱敏/编码任一逃逸
                        # （span 函数抛出，或 lone surrogate 的
                        # UnicodeEncodeError；机制同 shared/pi_events.
                        # _write_kept_event 的 fail-closed）——整行丢弃，绝不
                        # 把 raw 行写进 state 目录，也绝不因一行炸掉整批应急
                        # 转储（delivery 侧同款单行失败语义）。结果空间：转储
                        # 缺该行。日志保全：print。
                        print("emergency dump dropped an unrenderable line", flush=True)
                        continue
                    if self._written + len(payload) > _MAX_DUMP_TOTAL_BYTES:
                        self._capped = True
                        print(
                            f"emergency events dump capped at {_MAX_DUMP_TOTAL_BYTES}"
                            f" bytes: {self.path}",
                            flush=True,
                        )
                        return
                    dst.write(payload)
                    self._written += len(payload)
        except Exception as exc:
            # #204 broad-except audit: 应急转储写失败（state 目录不可写、
            # 磁盘满）是纯观测面降级——吞掉只记日志，事件面损失与既有
            # 行为（注销流）一致但不更糟；转储失败不得让 parse-pool 任务
            # 异常逃逸（那会注销流并丢掉后续全部事件）。日志保全：print。
            print(f"emergency events dump write failed for {self.path}: {exc!r}", flush=True)


def _render_line(line: bytes, redactor: SecretRedactor) -> str:
    """One framed line as redacted text: JSON events keep their structure
    (``redact_json`` — the same function the delivery path applies to
    archived events), non-JSON lines get span redaction. Capping happens
    AFTER redaction (redact first, cut after — the #748 discipline) and
    with an INLINE marker: one framed line stays ONE physical line
    (review P3-4 — a newline in the marker would split one event across
    three lines and corrupt line-based json.loads tooling)."""
    text = line.decode("utf-8", "replace")
    try:
        event = json.loads(text)
    except ValueError:
        event = None
    if isinstance(event, dict):
        redacted = redactor.redact_json(event)
        out = (
            text
            if redacted == event
            else json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
        )
    else:
        out = redactor.redact(text)
    if len(out) > _MAX_DUMP_LINE_CHARS:
        keep = _MAX_DUMP_LINE_CHARS // 2
        dropped = len(out) - _MAX_DUMP_LINE_CHARS
        out = f"{out[:keep]}[...{dropped} chars truncated...]{out[-keep:]}"
    return out + "\n"


def emergency_sink(
    execution_id: str, node_key: str, fallback: str = ""
) -> EmergencyEventsSink | None:
    """Open the emergency dump for one stream, or None when the evidence
    root is unconfigured — the caller then keeps its legacy degradation."""
    target = incident_dir(execution_id, node_key, fallback=fallback)
    if target is None:
        return None
    return EmergencyEventsSink(target / EMERGENCY_EVENTS_FILENAME, secret_snapshot())


def write_stream_batch(stream: Any, lines: list[bytes]) -> None:
    """One kept batch for one reactor pump stream: the events path — or,
    once that path stopped accepting writes (#1147, run dir deleted
    mid-run), the stream's emergency evidence sink.

    The stream STAYS registered either way, so the child's events keep
    flowing and the normal EOF/drain/``join`` lifecycle is untouched. On
    diversion the first failed batch is written to the sink and every later
    batch goes there too (redacted, bounded). Returns normally when the
    diversion engaged; re-raises the original OSError when no sink is
    available, so the reactor's legacy degradation (``parse_error`` +
    unregister, events lost) keeps its exact old semantics.

    ``stream`` is the reactor's ``_Stream``; it is passed as ``Any`` (duck
    typed: ``path`` / ``execution_id`` / ``node_key`` / ``evidence``) to
    keep this module free of an import back into ``worker.execution``.
    Called only by the stream's single-writer pool task, so the
    ``evidence`` engagement needs no extra lock."""
    evidence = stream.evidence
    if evidence is not None:
        evidence.write_lines(lines)
        return
    try:
        with open(stream.path, "ab") as output:
            for line in lines:
                output.write(line)
                output.write(b"\n")
    except OSError as exc:
        sink = emergency_sink(stream.execution_id, stream.node_key, fallback=stream.path)
        if sink is None:
            raise
        stream.evidence = sink
        print(
            f"event-pump write failed for {stream.path}: {exc!r}; "
            f"diverting this stream's events to {sink.path}",
            flush=True,
        )
        sink.write_lines(lines)
