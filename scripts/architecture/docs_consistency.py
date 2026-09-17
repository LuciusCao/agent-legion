"""Guard: code-side facts must not drift from their doc statements.

The retired-terms guard (docs_retired_terms.py) catches words that were
*removed*; this check catches the opposite failure mode exposed by #340
and its follow-up (#716): a live word whose *value* changed. "RustFS"
never retired — its default-ness did (SeaweedFS took over, #340), and no
retirement pattern can catch "README still says the default is RustFS".
Five docs tracked that stale default for two weeks until review-0916's
docs chapter caught them.

Facts asserted (code is the single source of truth, parsed from the live
files — no hardcoded mirrors that would themselves drift):

- ``SCHEMA_VERSION`` (schema.py) vs the "目前 vNN" statement in
  docs/materials-storage-deployment.md §3.3 — a schema bump PR that
  forgets the runbook is rejected.
- The default local storage backend name (local-s3-decide.sh) vs the
  README default-storage sentence, and its published S3 port
  (compose.host.yaml) vs the runbook's loopback endpoint statement.

Standalone entry mirrors the docs-terms CI job (--no-project, stdlib
only); the full gate path stays check_repository (repository.py).
"""

from __future__ import annotations

import re
from pathlib import Path

__test__ = False

SCHEMA_FILE = "server/app/db/schema.py"
SCHEMA_DOC = "docs/materials-storage-deployment.md"
DECIDE_SCRIPT = "scripts/local-s3-decide.sh"
COMPOSE_FILE = "deploy/compose.host.yaml"
README_FILES = ("README.md", "README_EN.md")

# The storage-deployment runbook states the live schema version as
# "目前 vNN" right where it enumerates recent migrations.
_SCHEMA_DOC_PATTERN = re.compile(r"（目前 v(\d+)）")
_SCHEMA_SOURCE_PATTERN = re.compile(r"^SCHEMA_VERSION\s*=\s*(\d+)\s*$", re.MULTILINE)

# local-s3-decide.sh resolves the default backend as
#   BACKEND="${BACKEND:-seaweedfs}"
_DECIDE_DEFAULT_PATTERN = re.compile(r'BACKEND="\$\{BACKEND:-(\w+)\}"')
# compose publishes the default backend's S3 port as a mapping line
# (":8333:8333"); read it off the seaweedfs service block.
_COMPOSE_S3_PORT_PATTERN = re.compile(
    r"^\s*-\s*\$\{AGENT_LEGION_S3_BIND[^}]*\}:(\d+):(\d+)\s*$", re.MULTILINE
)

# README states the default backend as a bolded sentence; both languages
# keep the same "local **Name**" shape ("本地 **SeaweedFS**" /
# "local **SeaweedFS**"), so one pattern covers both files.
_README_DEFAULT_BACKEND_PATTERN = re.compile(r"(?:本地|local)\s+\*\*(\w+)\*\*")

# The storage runbook states the default local endpoint as a loopback
# URL next to AGENT_LEGION_S3_ENDPOINT ("`http://127.0.0.1:8333`");
# its port half is asserted against the compose-parsed S3 port.
_SCHEMA_DOC_ENDPOINT_PATTERN = re.compile(r"`http://127\.0\.0\.1:(\d+)`")


class DocsConsistencySourceError(ValueError):
    """A code-side source file cannot be parsed — fail closed."""


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise DocsConsistencySourceError(f"source file missing: {relative}")
    return path.read_text(encoding="utf-8", errors="replace")


def read_schema_version(root: Path) -> int:
    match = _SCHEMA_SOURCE_PATTERN.search(_read(root, SCHEMA_FILE))
    if match is None:
        raise DocsConsistencySourceError(
            f"{SCHEMA_FILE}: cannot find 'SCHEMA_VERSION = <int>' "
            "(if renamed or reformatted, update scripts/architecture/docs_consistency.py)"
        )
    return int(match.group(1))


def read_default_backend(root: Path) -> str:
    """Default local backend name, from local-s3-decide.sh's own default."""
    match = _DECIDE_DEFAULT_PATTERN.search(_read(root, DECIDE_SCRIPT))
    if match is None:
        raise DocsConsistencySourceError(
            f"{DECIDE_SCRIPT}: cannot find the BACKEND default assignment "
            "(if the resolution shape changed, update docs_consistency.py)"
        )
    return match.group(1)


def read_default_backend_port(root: Path) -> int:
    """S3 port of the default backend, from the compose service mapping."""
    backend = read_default_backend(root)
    compose = _read(root, COMPOSE_FILE)
    service_block = re.search(
        rf"^  {backend}:\n((?:(?!^  \w).)*)", compose, re.MULTILINE | re.DOTALL
    )
    if service_block is None:
        raise DocsConsistencySourceError(
            f"{COMPOSE_FILE}: cannot find the '{backend}' service block "
            "(if the service was renamed, update docs_consistency.py)"
        )
    match = _COMPOSE_S3_PORT_PATTERN.search(service_block.group(1))
    if match is None:
        raise DocsConsistencySourceError(
            f"{COMPOSE_FILE}: no AGENT_LEGION_S3_BIND port mapping in the "
            f"'{backend}' service (if the publish moved, update docs_consistency.py)"
        )
    return int(match.group(1))


def check_docs_consistency(root: Path) -> list[str]:
    """Reject docs whose stated facts diverge from the code's live values.

    Fixture repos (the check_repository test suites run check_repository
    against minimal fake trees) have none of the source files this guard
    parses; like docs_retired_terms' index reconciliation, the guard is
    a no-op there rather than a failure. A PARTIAL set of sources (one or
    two of them exist) is never skipped — on the real repo that means a
    renamed/moved source fails closed instead of silently disabling the
    guard (codex review on #744)."""
    root = root.resolve()
    source_paths = (SCHEMA_FILE, DECIDE_SCRIPT, COMPOSE_FILE)
    existing = {path for path in source_paths if (root / path).is_file()}
    if not existing:
        return []
    errors: list[str] = [
        f"docs consistency: source file missing: {path} "
        "(renamed or moved — update scripts/architecture/docs_consistency.py)"
        for path in source_paths
        if path not in existing
    ]
    # Facts are grouped by source: schema facts need SCHEMA_FILE; backend/
    # port facts need DECIDE_SCRIPT + COMPOSE_FILE. A group runs when its
    # sources exist, so one missing source never silences the others.

    # --- Schema version: migration runbook must state the live value ---
    doc_text = _read(root, SCHEMA_DOC) if (root / SCHEMA_DOC).is_file() else ""
    if SCHEMA_FILE in existing:
        try:
            live = read_schema_version(root)
        except DocsConsistencySourceError as exc:
            return errors + [f"docs consistency: {exc}"]
        match = _SCHEMA_DOC_PATTERN.search(doc_text)
        if match is None:
            errors.append(
                f"{SCHEMA_DOC}: cannot find the '（目前 vNN）' schema-version "
                f"statement — keep one matching {SCHEMA_FILE} (currently v{live})"
            )
        elif int(match.group(1)) != live:
            errors.append(
                f"{SCHEMA_DOC}: states schema v{match.group(1)} but {SCHEMA_FILE} "
                f"has SCHEMA_VERSION = {live} — bump the doc with the migration"
            )

    # --- Default storage backend + port: READMEs and the storage runbook
    # must state the live ones (a parsed-but-unasserted port guards
    # nothing — codex review on #744; the rustfs escape hatch may keep its
    # own ":9000" mention, only the default endpoint URL is asserted). ---
    if {DECIDE_SCRIPT, COMPOSE_FILE} <= existing:
        try:
            backend = read_default_backend(root)
            port = read_default_backend_port(root)
        except DocsConsistencySourceError as exc:
            errors.append(f"docs consistency: {exc}")
            return errors
        for readme in README_FILES:
            match = _README_DEFAULT_BACKEND_PATTERN.search(_read(root, readme))
            if match is None:
                errors.append(
                    f"{readme}: cannot find the 'local **Backend**' default-storage "
                    f"sentence — keep stating the default (currently {backend})"
                )
            elif match.group(1).lower() != backend.lower():
                errors.append(
                    f"{readme}: default-storage sentence names **{match.group(1)}** "
                    f"but {DECIDE_SCRIPT} defaults to '{backend}' — update the "
                    f"README and its S3 port references to :{port}"
                )
        endpoint = _SCHEMA_DOC_ENDPOINT_PATTERN.search(doc_text)
        if endpoint is None:
            errors.append(
                f"{SCHEMA_DOC}: cannot find the '`http://127.0.0.1:<port>`' "
                f"default-endpoint statement — keep stating the default backend's "
                f"S3 port (currently :{port} in {COMPOSE_FILE})"
            )
        elif int(endpoint.group(1)) != port:
            errors.append(
                f"{SCHEMA_DOC}: default endpoint states :{endpoint.group(1)} but "
                f"{COMPOSE_FILE} publishes :{port} — update the doc with the port"
            )
    return sorted(errors)


if __name__ == "__main__":
    # Standalone entry for the CI docs-terms job (same --no-project env)
    # and for local debugging; the full gate path stays check_repository.
    repo_root = Path(__file__).resolve().parents[2]
    failures = check_docs_consistency(repo_root)
    for failure in failures:
        print(f"ERROR: {failure}")
    raise SystemExit(1 if failures else 0)
