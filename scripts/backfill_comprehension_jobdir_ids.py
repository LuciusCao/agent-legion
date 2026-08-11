"""Sync fixed comprehension ids into job-directory files and reset ``packed``.

``scripts.backfill_comprehension_ids`` repaired the content-addressed
artifact store, but ``create_workspace_package`` zips files straight from
the job directory (``<data_dir>/<storage_dir>``), whose root JSON files
still carry the pre-fix fake ids — including ``comprehension_info.json``,
which is assembled from those files and is not in ``artifact_refs`` at all.

This follow-up script closes the gap:

1. Find the refs rewritten by the artifact backfill:
   ``artifact_refs join artifacts on hash`` with ``created_at > --since``,
   restricted to the seven comprehension names, deduped by (job_id, name).
2. Rebuild the old -> new id mapping per job by positionally aligning the
   pe/ki token sequence of the job-dir file (old content) against the new
   blob; when lengths differ, a single-element symmetric set difference is
   paired instead. Anything else, or a conflicting mapping within the job,
   skips the job with a warning.
3. Sync the job directory (jobs with an active agent_execution_requests row
   are skipped): the seven names are overwritten with their new blob bytes
   (the authoritative content); every other root text file (not recursing
   into ``runs/``) gets exact whole-token replacement from the mapping.
   Tokens still non-compliant afterwards are counted as leftovers.
4. ``--apply`` resets ``packed`` to 0 for the processed jobs so they get
   repacked and re-uploaded.

Usage:
    uv run python -m scripts.backfill_comprehension_jobdir_ids \
        --database-url <DSN> --artifacts-dir <path> --data-dir <path> \
        --since '<ISO timestamp>' [--apply] [--verify]

Default is dry-run (report only); ``--apply`` writes; ``--verify`` re-scans
job-dir root files and reports residual bad tokens.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scripts.backfill_comprehension_ids import ACTIVE_STATES, SCAN_NAMES
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.settings import load_settings

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"\b(?:pe|ki)_[0-9A-Za-z][0-9A-Za-z-]*")
_STRICT_UUID4 = re.compile(
    r"^(pe|ki)_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _rewritten_refs(dsn: DatabaseDsn, since: datetime) -> dict[str, dict[str, str]]:
    """job_id -> {name: new_hash} for refs whose blob was created after ``since``."""
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select r.job_id, r.name, r.hash, max(a.created_at) as created_at"
            " from artifact_refs r join artifacts a on a.hash = r.hash"
            " where a.created_at > %s and r.name = any(%s)"
            " group by r.job_id, r.name, r.hash"
            " order by r.job_id, r.name, created_at desc",
            (since, list(SCAN_NAMES)),
        ).fetchall()
    refs: dict[str, dict[str, str]] = defaultdict(dict)
    seen: set[tuple[str, str]] = set()
    for row in rows:  # first row per (job, name) wins: the latest created blob
        key = (row["job_id"], row["name"])
        if key in seen:
            logger.warning(
                "job %s %s: multiple rewritten blobs in window; using latest",
                row["job_id"],
                row["name"],
            )
            continue
        seen.add(key)
        refs[row["job_id"]][row["name"]] = row["hash"]
    return dict(refs)


def _active_job_ids(dsn: DatabaseDsn) -> set[str]:
    with read_connection(dsn) as conn:
        rows = conn.execute(
            "select distinct job_id from agent_execution_requests where state = any(%s)",
            (list(ACTIVE_STATES),),
        ).fetchall()
    return {row["job_id"] for row in rows}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    staging = path.with_name(f"{path.name}.tmp-{uuid.uuid4()}")
    try:
        staging.write_bytes(data)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _pair_tokens(old_text: str, new_text: str) -> dict[str, str] | None:
    """Positionally align pe/ki token sequences; None when unalignable."""
    old_tokens = _TOKEN.findall(old_text)
    new_tokens = _TOKEN.findall(new_text)
    if len(old_tokens) == len(new_tokens):
        return dict(zip(old_tokens, new_tokens, strict=True))
    old_only = set(old_tokens) - set(new_tokens)
    new_only = set(new_tokens) - set(old_tokens)
    if len(old_only) == 1 and len(new_only) == 1:
        return {old_only.pop(): new_only.pop()}
    return None


def _build_job_mapping(
    job_id: str, job_dir: Path, artifacts_dir: Path, names: dict[str, str]
) -> dict[str, str] | None:
    """Merge per-file token pairings; None on unalignable file or conflict."""
    mapping: dict[str, str] = {}
    for name, new_hash in sorted(names.items()):
        old_text = _read_text(job_dir / name)
        if old_text is None:
            logger.warning("job %s: job-dir file %s missing/unreadable; skipping", job_id, name)
            return None
        new_text = _read_text(artifacts_dir / new_hash[:2] / new_hash)
        if new_text is None:
            logger.warning("job %s: blob %s unreadable; skipping", job_id, new_hash)
            return None
        pairing = _pair_tokens(old_text, new_text)
        if pairing is None:
            logger.warning("job %s %s: token sequences not alignable; skipping job", job_id, name)
            return None
        for old, new in pairing.items():
            if old == new:
                continue
            if old in mapping and mapping[old] != new:
                logger.warning(
                    "job %s: token %r maps to both %r and %r; skipping job",
                    job_id,
                    old,
                    mapping[old],
                    new,
                )
                return None
            mapping[old] = new
    return mapping


def _replace_tokens(text: str, mapping: dict[str, str]) -> str:
    """Whole-token replacement; lookarounds keep prefixes of longer ids safe."""
    for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(r"(?<![0-9A-Za-z_-])" + re.escape(old) + r"(?![0-9A-Za-z-])")
        text = pattern.sub(new, text)
    return text


def _bad_tokens(text: str) -> list[str]:
    return sorted({t for t in _TOKEN.findall(text) if not _STRICT_UUID4.match(t)})


@dataclass
class SyncStats:
    affected_jobs: int = 0
    skipped_active_jobs: int = 0
    skipped_jobs: int = 0  # unalignable / conflicting mapping
    mapping_entries: int = 0
    files_overwritten: int = 0
    files_token_synced: int = 0
    leftover_tokens: int = 0
    packed_reset: int = 0
    applied: bool = False


def sync_job_dirs(
    dsn: DatabaseDsn, artifacts_dir: Path, data_dir: Path, since: datetime, *, apply: bool = False
) -> SyncStats:
    artifacts_dir = Path(artifacts_dir)
    data_dir = Path(data_dir)
    rewritten = _rewritten_refs(dsn, since)
    active_jobs = _active_job_ids(dsn)
    stats = SyncStats(affected_jobs=len(rewritten), applied=apply)

    with read_connection(dsn) as conn:
        job_rows = {
            row["id"]: row
            for row in conn.execute(
                "select id, storage_dir, packed from jobs where id = any(%s)",
                (sorted(rewritten),),
            ).fetchall()
        }

    processed: list[str] = []
    for job_id in sorted(rewritten):
        if job_id in active_jobs:
            stats.skipped_active_jobs += 1
            continue
        job_row = job_rows.get(job_id)
        job_dir = data_dir / job_row["storage_dir"] if job_row else None
        if job_dir is None or not job_dir.is_dir():
            logger.warning("job %s: job directory missing; skipping", job_id)
            stats.skipped_jobs += 1
            continue
        mapping = _build_job_mapping(job_id, job_dir, artifacts_dir, rewritten[job_id])
        if mapping is None:
            stats.skipped_jobs += 1
            continue
        stats.mapping_entries += len(mapping)
        # (a) Overwrite the seven names with their authoritative new blobs.
        for name, new_hash in sorted(rewritten[job_id].items()):
            blob = (artifacts_dir / new_hash[:2] / new_hash).read_bytes()
            target = job_dir / name
            if target.is_file() and target.read_bytes() == blob:
                continue
            stats.files_overwritten += 1
            if apply:
                _atomic_write(target, blob)
        # (b) Token-sync every other root text file (runs/ not recursed).
        for path in sorted(job_dir.iterdir()):
            if not path.is_file() or path.name in SCAN_NAMES:
                continue
            text = _read_text(path)
            if text is None:
                continue
            new_text = _replace_tokens(text, mapping)
            leftovers = _bad_tokens(new_text)
            if leftovers:
                stats.leftover_tokens += len(leftovers)
                logger.warning(
                    "job %s %s: %d bad token(s) left: %s",
                    job_id,
                    path.name,
                    len(leftovers),
                    leftovers,
                )
            if new_text != text:
                stats.files_token_synced += 1
                if apply:
                    _atomic_write(path, new_text.encode("utf-8"))
        processed.append(job_id)

    packed_jobs = [j for j in processed if int(job_rows[j]["packed"]) == 1]
    stats.packed_reset = len(packed_jobs)
    if apply and packed_jobs:
        with write_transaction(dsn) as conn:
            cursor = conn.execute(
                "update jobs set packed = 0 where id = any(%s) and packed = 1",
                (packed_jobs,),
            )
            stats.packed_reset = cursor.rowcount
    return stats


def verify(dsn: DatabaseDsn, data_dir: Path, since: datetime) -> tuple[int, int]:
    """Re-scan job-dir root files; return (bad tokens, of which in active jobs)."""
    rewritten = _rewritten_refs(dsn, since)
    active_jobs = _active_job_ids(dsn)
    with read_connection(dsn) as conn:
        job_rows = {
            row["id"]: row["storage_dir"]
            for row in conn.execute(
                "select id, storage_dir from jobs where id = any(%s)",
                (sorted(rewritten),),
            ).fetchall()
        }
    bad = 0
    in_active = 0
    for job_id, storage_dir in job_rows.items():
        job_dir = Path(data_dir) / storage_dir
        if not job_dir.is_dir():
            continue
        count = 0
        for path in sorted(job_dir.iterdir()):
            if not path.is_file():
                continue
            text = _read_text(path)
            if text is not None:
                count += len(_bad_tokens(text))
        bad += count
        if job_id in active_jobs:
            in_active += count
    return bad, in_active


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=None, help="Override the configured AGENT_LEGION_DATABASE_URL."
    )
    parser.add_argument(
        "--artifacts-dir", default=None, help="Artifact store root (default: <data_dir>/artifacts)."
    )
    parser.add_argument("--data-dir", default=None, help="Data root holding jobs/ storage_dirs.")
    parser.add_argument(
        "--since",
        required=True,
        help="ISO timestamp; refs whose blob was created after this are the rewritten set.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default is dry-run, report only)."
    )
    parser.add_argument(
        "--verify", action="store_true", help="Re-scan job dirs and report residual bad tokens."
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else data_dir / "artifacts"
    since = datetime.fromisoformat(args.since)

    stats = sync_job_dirs(dsn, artifacts_dir, data_dir, since, apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"{mode}: affected jobs={stats.affected_jobs} "
        f"(skipped active={stats.skipped_active_jobs}, skipped unalignable={stats.skipped_jobs}); "
        f"mapping entries={stats.mapping_entries}; "
        f"files {'overwritten' if args.apply else 'to overwrite'}={stats.files_overwritten}, "
        f"token-synced={stats.files_token_synced}; "
        f"leftover bad tokens={stats.leftover_tokens}; "
        f"packed {'reset' if args.apply else 'to reset'}={stats.packed_reset}"
    )
    if args.verify:
        bad, in_active = verify(dsn, data_dir, since)
        print(f"verify: residual bad tokens in job dirs={bad} (in active jobs={in_active})")


if __name__ == "__main__":
    main()
