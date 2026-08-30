#!/usr/bin/env python3
"""Refresh the tracked GitHub issue-state cache for exemption expiry detection.

Collects every ``issues/<open|closed>/github.com/<owner>/<repo>/issues/<n>``
reference from the exemption registry and resolves the current state of each
distinct issue via a single ``gh issue list`` call per repository. The result
is written to ``config/architecture/issue-states.json``, the tracked manifest
that ``scripts.check_invariants`` reads offline. Run via
``make architecture-issue-states`` (also the manual fallback for the nightly
CI job) whenever anchors close or exemptions are added.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.quality.exemptions import load_exemptions
from scripts.quality.issue_state import (
    MANIFEST_RELATIVE_PATH,
    MANIFEST_VERSION,
    parse_issue_reference,
)

project_root = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def _repo_key(owner: str, name: str) -> str:
    return f"{owner}/{name}"


def collect_issue_references(
    exemptions_path: Path,
) -> dict[str, set[int]]:
    """Map each referenced repository to the set of referenced issue numbers."""
    references: dict[str, set[int]] = {}
    for ex in load_exemptions(exemptions_path):
        parsed = parse_issue_reference(ex.remove_when)
        if parsed is None:
            continue
        parts = parsed[1].split("/")
        owner, name, number = parts[1], parts[2], int(parts[4])
        references.setdefault(_repo_key(owner, name), set()).add(number)
    return references


def fetch_issue_states(
    references: dict[str, set[int]],
) -> dict[str, str]:
    """Resolve the current state of every referenced issue via gh."""
    states: dict[str, str] = {}
    for repo_key, numbers in sorted(references.items()):
        # One call per repository with an explicit number filter keeps this
        # at N API requests for N referenced repositories regardless of how
        # many exemptions point at them.
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo_key,
                "--state",
                "all",
                "--limit",
                "1000",
                "--json",
                "number,state",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"gh issue list failed for {repo_key}: {message}")
        for issue in json.loads(result.stdout):
            if issue.get("number") in numbers:
                reference = f"github.com/{repo_key}/issues/{issue['number']}"
                states[reference] = str(issue["state"]).lower()
    return states


def refresh_issue_states(root: Path) -> dict[str, str]:
    """Refresh the manifest from the live exemption registry."""
    references = collect_issue_references(root / "config/architecture/architecture-exemptions.yaml")
    return fetch_issue_states(references)


def write_manifest(root: Path, states: dict[str, str]) -> Path:
    """Write the issue-state manifest atomically and return its path."""
    manifest_path = root / MANIFEST_RELATIVE_PATH
    payload = {
        "version": MANIFEST_VERSION,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "issues": dict(sorted(states.items())),
    }
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=manifest_path.parent, suffix=".tmp", delete=False
    ) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
        tmp_path = f.name

    os.replace(tmp_path, manifest_path)
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the issue-state cache for exemption expiry detection."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=project_root,
        help="Project root holding the exemption registry and manifest.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = args.root.resolve()

    try:
        states = refresh_issue_states(root)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    manifest_path = write_manifest(root, states)
    closed = sorted(reference for reference, state in states.items() if state == "closed")
    logger.info(
        "OK: wrote %s with %d issue state(s) (%d closed)",
        manifest_path.relative_to(root),
        len(states),
        len(closed),
    )
    for reference in closed:
        logger.warning("closed: %s", reference)
    return 0


if __name__ == "__main__":
    sys.exit(main())
