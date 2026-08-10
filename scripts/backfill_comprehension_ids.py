"""Backfill hand-crafted fake ids in question_comprehension artifacts.

Historical agents invented ``error_id`` (``pe_`` prefix) and ``key_info_id``
(``ki_`` prefix) values instead of generating contract-shaped ids
(``pe_{uuid4}`` / ``ki_{uuid4}``, lowercase, version nibble ``4``, variant
nibble in ``{8,9,a,b}``). This one-off script rewrites the content-addressed
artifact store:

1. Scan the seven comprehension artifact names and build a global id -> jobs
   map (structured fields for raw/reviewed files, token regex for the
   free-text review reports).
2. An id needs replacement when its suffix is not a strict uuid4, or when the
   same id appears in more than one job (proven collision). uuid4-shaped
   unique ids are left alone even if they look hand-picked.
3. Rewrite per job, in one transaction per job: jobs with an active
   ``agent_execution_requests`` row (queued/claimed/reporting) are skipped.
   Every artifact of the job (all names, not just the seven) gets exact
   full-token replacement of each bad id; changed blobs are written to the
   store and the ref hash is updated. Reruns are naturally idempotent:
   already-fixed ids are compliant and unique, so they never match again.

Id mapping invariants: within one job the same old id maps to the same new
id (raw/reviewed/report stay consistent, and ``related_key_info_ids``
references follow a regenerated ki id); across jobs the same old id maps to
different new ids. A bad id that prefixes exactly one compliant id in the
same job (an agent-truncated reference in report prose) is re-pointed to
that full id instead of minting an unrelated random one.

Usage:
    uv run python -m scripts.backfill_comprehension_ids \
        --database-url <DSN> --artifacts-dir <path> [--apply] [--verify]

Default is dry-run (report only); ``--apply`` writes; ``--verify`` re-scans
and reports residual bad ids (only skipped active jobs should remain).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.settings import load_settings

logger = logging.getLogger(__name__)

STRUCTURED_PE_NAMES = ("possible_errors_raw.json", "possible_errors_reviewed.json")
STRUCTURED_KI_NAMES = (
    "key_info_raw.json",
    "key_info_reviewed.json",
    "key_info_reviewed_lean.json",
)
REPORT_NAMES = ("possible_errors_review_report.json", "key_info_review_report.json")
SCAN_NAMES = STRUCTURED_PE_NAMES + STRUCTURED_KI_NAMES + REPORT_NAMES

ACTIVE_STATES = ("queued", "claimed", "reporting")

_STRICT_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Free-text reports embed ids as plain tokens; unprefixed literals such as
# "placeholder" are picked up from the structured fields instead.
_REPORT_TOKEN = re.compile(r"\b(?:pe|ki)_[0-9A-Za-z][0-9A-Za-z-]*")


def _is_compliant(id_value: str) -> bool:
    if not id_value.startswith(("pe_", "ki_")):
        return False
    return _STRICT_UUID4.match(id_value[3:]) is not None


def _extract_ids(name: str, text: str) -> set[tuple[str, str]]:
    """Return {(id, kind)} where kind is 'pe' or 'ki' (drives the new prefix)."""
    ids: set[tuple[str, str]] = set()
    parsed = None
    if name in STRUCTURED_PE_NAMES + STRUCTURED_KI_NAMES:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("unparseable JSON in %s; falling back to token scan", name)
    if name in STRUCTURED_PE_NAMES and parsed is not None:
        for item in (parsed or {}).get("possible_error_list") or []:
            error_id = item.get("error_id")
            if isinstance(error_id, str) and error_id:
                ids.add((error_id, "pe"))
            for ref in item.get("related_key_info_ids") or []:
                if isinstance(ref, str) and ref:
                    ids.add((ref, "ki"))
    elif name in STRUCTURED_KI_NAMES and parsed is not None:
        for item in (parsed or {}).get("key_info_list") or []:
            key_info_id = item.get("key_info_id")
            if isinstance(key_info_id, str) and key_info_id:
                ids.add((key_info_id, "ki"))
    else:
        for token in _REPORT_TOKEN.findall(text):
            ids.add((token, token[:2]))
    return ids


def _read_blob(artifacts_dir: Path, hash: str) -> str | None:
    path = artifacts_dir / hash[:2] / hash
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("skipping unreadable artifact %s: %s", hash, exc)
        return None


@dataclass
class ScanResult:
    id_jobs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    id_kinds: dict[str, str] = field(default_factory=dict)

    @property
    def bad_ids(self) -> set[str]:
        return {
            id_value
            for id_value, jobs in self.id_jobs.items()
            if not _is_compliant(id_value) or len(jobs) > 1
        }


def scan(dsn: DatabaseDsn, artifacts_dir: Path) -> ScanResult:
    """Pass 1: build the global id -> jobs map from the seven scan names.

    Rows are ordered so that identical (hash, name) pairs are consecutive:
    the same blob is often referenced by several nodes (generate/review/
    assess), and only the first of each run is read and parsed.
    """
    result = ScanResult()
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select job_id, name, hash from artifact_refs where name = any(%s)"
            " order by hash, name",
            (list(SCAN_NAMES),),
        ).fetchall()
    last_key: tuple[str, str] | None = None
    last_ids: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["hash"], row["name"])
        if key != last_key:
            text = _read_blob(artifacts_dir, row["hash"])
            last_ids = _extract_ids(row["name"], text) if text is not None else set()
            last_key = key
        for id_value, kind in last_ids:
            result.id_jobs[id_value].add(row["job_id"])
            result.id_kinds.setdefault(id_value, kind)
    return result


def _active_job_ids(dsn: DatabaseDsn) -> set[str]:
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select distinct job_id from agent_execution_requests where state = any(%s)",
            (list(ACTIVE_STATES),),
        ).fetchall()
    return {row["job_id"] for row in rows}


def _new_id(old_id: str, kinds: dict[str, str], taken: set[str]) -> str:
    if old_id.startswith(("pe_", "ki_")):
        prefix = old_id[:3]
    else:
        # Unprefixed literals (e.g. "placeholder") were seen as ki ids.
        prefix = "ki_" if kinds.get(old_id, "ki") == "ki" else "pe_"
    while True:
        candidate = f"{prefix}{uuid.uuid4()}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate


def _resolve_new_id(old_id: str, job_id: str, scan_result: ScanResult, taken: set[str]) -> str:
    """Map a bad id to its replacement for one job.

    Agents sometimes truncated their own compliant id in free-text reports
    (``ki_460bd7aa`` for ``ki_460bd7aa-6c83-...``). When the bad id is a
    prefix of exactly one compliant id in the same job, re-point to that
    full id instead of minting an unrelated random one that would leave the
    reference dangling. Ambiguous or extensionless ids get a fresh uuid4.
    """
    extensions = {
        candidate
        for candidate, jobs in scan_result.id_jobs.items()
        if job_id in jobs
        and candidate != old_id
        and _is_compliant(candidate)
        and candidate.startswith(old_id)
    }
    if len(extensions) == 1:
        return extensions.pop()
    if len(extensions) > 1:
        logger.warning(
            "job %s: bad id %r is a prefix of %d compliant ids; minting a fresh one",
            job_id,
            old_id,
            len(extensions),
        )
    return _new_id(old_id, scan_result.id_kinds, taken)


def _apply_mapping(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """Replace full-id tokens; return (new text, unsafe leftover ids).

    Prefixed ids are replaced as whole tokens (boundary lookarounds prevent
    eating a prefix of a longer id). Unprefixed literals are only replaced as
    exact JSON string values — a bare word like ``placeholder`` in free text
    is left untouched and reported instead of risking prose corruption.
    """
    leftovers: list[str] = []
    for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if old.startswith(("pe_", "ki_")):
            pattern = re.compile(r"(?<![0-9A-Za-z_-])" + re.escape(old) + r"(?![0-9A-Za-z-])")
            text = pattern.sub(new, text)
        else:
            text = text.replace(f'"{old}"', f'"{new}"')
            if re.search(r"(?<![0-9A-Za-z_-])" + re.escape(old) + r"(?![0-9A-Za-z_-])", text):
                leftovers.append(old)
    return text, leftovers


def _publish_blob(artifacts_dir: Path, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    final = artifacts_dir / digest[:2] / digest
    if not final.exists():
        staging = artifacts_dir / ".staging" / str(uuid.uuid4())
        try:
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.write_bytes(data)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
        finally:
            staging.unlink(missing_ok=True)
    return digest


@dataclass
class BackfillStats:
    bad_pe: int = 0
    bad_ki: int = 0
    affected_jobs: int = 0
    skipped_active_jobs: int = 0
    rewritten_artifacts: int = 0
    applied: bool = False


def backfill_comprehension_ids(
    dsn: DatabaseDsn, artifacts_dir: Path, *, apply: bool = False
) -> BackfillStats:
    artifacts_dir = Path(artifacts_dir)
    scan_result = scan(dsn, artifacts_dir)
    bad_ids = scan_result.bad_ids
    active_jobs = _active_job_ids(dsn)

    affected = {job for id_value in bad_ids for job in scan_result.id_jobs[id_value]}
    runnable = sorted(affected - active_jobs)
    stats = BackfillStats(
        bad_pe=sum(1 for i in bad_ids if scan_result.id_kinds.get(i) == "pe"),
        bad_ki=sum(1 for i in bad_ids if scan_result.id_kinds.get(i) != "pe"),
        affected_jobs=len(affected),
        skipped_active_jobs=len(affected & active_jobs),
        applied=apply,
    )

    taken = set(scan_result.id_jobs)
    for job_id in runnable:
        mapping = {
            old: _resolve_new_id(old, job_id, scan_result, taken)
            for old in sorted(bad_ids)
            if job_id in scan_result.id_jobs[old]
        }
        if not mapping:
            continue
        with read_connection(dsn) as conn:
            refs = conn.execute(
                "select node_key, name, hash from artifact_refs where job_id = %s",
                (job_id,),
            ).fetchall()
        changes: list[tuple[dict, bytes]] = []
        for ref in refs:
            text = _read_blob(artifacts_dir, ref["hash"])
            if text is None:
                continue
            new_text, leftovers = _apply_mapping(text, mapping)
            for old in leftovers:
                logger.warning(
                    "job %s %s: bare unprefixed id %r left untouched in free text",
                    job_id,
                    ref["name"],
                    old,
                )
            if new_text != text:
                changes.append((ref, new_text.encode("utf-8")))
        stats.rewritten_artifacts += len(changes)
        if not apply or not changes:
            continue
        new_hashes = [(ref, _publish_blob(artifacts_dir, data)) for ref, data in changes]
        with write_transaction(dsn) as conn:
            for (ref, digest), (_, data) in zip(new_hashes, changes, strict=True):
                conn.execute(
                    "insert into artifacts(hash, size) values (%s, %s) on conflict(hash) do nothing",
                    (digest, len(data)),
                )
                conn.execute(
                    "update artifact_refs set hash = %s"
                    " where job_id = %s and node_key = %s and name = %s",
                    (digest, job_id, ref["node_key"], ref["name"]),
                )
    return stats


def verify(dsn: DatabaseDsn, artifacts_dir: Path) -> tuple[int, int]:
    """Re-scan; return (residual bad ids, of which in currently active jobs)."""
    scan_result = scan(dsn, artifacts_dir)
    active_jobs = _active_job_ids(dsn)
    bad = scan_result.bad_ids
    in_active = sum(
        1 for i in bad if scan_result.id_jobs[i] and scan_result.id_jobs[i] <= active_jobs
    )
    return len(bad), in_active


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=None, help="Override the configured AGENT_LEGION_DATABASE_URL."
    )
    parser.add_argument(
        "--artifacts-dir", default=None, help="Artifact store root (default: <data_dir>/artifacts)."
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default is dry-run, report only)."
    )
    parser.add_argument(
        "--verify", action="store_true", help="Re-scan afterwards and report residual bad ids."
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    artifacts_dir = (
        Path(args.artifacts_dir) if args.artifacts_dir else settings.data_dir / "artifacts"
    )

    stats = backfill_comprehension_ids(dsn, artifacts_dir, apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"{mode}: bad ids pe={stats.bad_pe} ki={stats.bad_ki}; "
        f"affected jobs={stats.affected_jobs} (skipped active={stats.skipped_active_jobs}); "
        f"artifacts {'rewritten' if args.apply else 'to rewrite'}={stats.rewritten_artifacts}"
    )
    if args.verify:
        residual, in_active = verify(dsn, artifacts_dir)
        print(f"verify: residual bad ids={residual} (in active jobs={in_active})")


if __name__ == "__main__":
    main()
