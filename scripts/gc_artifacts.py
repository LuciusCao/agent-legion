"""Report or reclaim orphan blobs in the content-addressed artifact store.

Dry-run by default: prints (count, bytes) of zero-reference artifacts past
the in-flight grace window — the blobs the job-deletion GC path can never
see (lost result reports, crashed Workers, pre-claim cleanups). ``--apply``
reclaims them through ``ArtifactStore.delete_unreferenced``, which
transactionally re-checks refcounts and the grace window before unlinking.

Usage:
    uv run python -m scripts.gc_artifacts [--apply] [--database-url ...]
"""

from __future__ import annotations

import argparse

from server.app.services.artifact_orphan_gc import gc_orphans, orphan_stats
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reclaim orphan blobs (default: dry-run statistics only).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured AGENT_LEGION_DATABASE_URL.",
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    store = ArtifactStore(settings.data_dir / "artifacts", dsn)
    if args.apply:
        reclaimed = gc_orphans(store)
        print(f"reclaimed {reclaimed} orphan artifact blob(s)")
    else:
        count, total_bytes = orphan_stats(store)
        print(f"dry-run: {count} orphan artifact blob(s), {total_bytes} byte(s)")


if __name__ == "__main__":
    main()
