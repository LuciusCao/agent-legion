"""docs_consistency guard tests: code-side facts vs doc statements.

The fixture style mirrors tests/scripts/test_architecture_docs_retired_terms.py
— minimal fake repos under tmp_path — plus two live-tree smoke cases: the
guard must PASS on the real repo (docs and code agree) and must FAIL when
a fact is drifted in the fixture. The #340/#716 incident (five docs kept
saying "default RustFS" weeks after SeaweedFS took over) is the failure
mode this suite pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture.docs_consistency import (
    DocsConsistencySourceError,
    check_docs_consistency,
    read_default_backend,
    read_default_backend_port,
    read_schema_version,
)

SCHEMA_FILE = "server/app/db/schema.py"
SCHEMA_DOC = "docs/materials-storage-deployment.md"
DECIDE_SCRIPT = "scripts/local-s3-decide.sh"
COMPOSE_FILE = "deploy/compose.host.yaml"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repo(
    root: Path,
    *,
    schema_version: int = 82,
    doc_schema_version: int | None = None,
    backend: str = "seaweedfs",
    readme_backend: str = "SeaweedFS",
    doc_port: int = 8333,
    compose_port: int = 8333,
) -> None:
    stated_version = doc_schema_version if doc_schema_version is not None else schema_version
    _write(root, SCHEMA_FILE, f"SCHEMA_VERSION = {schema_version}\n")
    _write(
        root,
        SCHEMA_DOC,
        "- 当前 schema 版本以 `server/app/db/schema.py` 的 `SCHEMA_VERSION` 为准\n"
        f"  （目前 v{stated_version}）。近期迁移随启动自动执行。\n"
        f"- 原生形态的 `AGENT_LEGION_S3_ENDPOINT` 默认指向\n"
        f"  `http://127.0.0.1:{doc_port}`（rustfs 逃生舱为 `:9000`）。\n",
    )
    _write(
        root,
        DECIDE_SCRIPT,
        "#!/usr/bin/env bash\n"
        'BACKEND="$(lookup AGENT_LEGION_LOCAL_S3_BACKEND)"\n'
        f'BACKEND="${{BACKEND:-{backend}}}"\n',
    )
    _write(
        root,
        COMPOSE_FILE,
        "services:\n"
        f"  {backend}:\n"
        "    image: example/backend:1\n"
        "    ports:\n"
        f"      - ${{AGENT_LEGION_S3_BIND:-127.0.0.1}}:{compose_port}:{compose_port}\n"
        "      - ${AGENT_LEGION_S3_BIND:-127.0.0.1}:9333:9333\n",
    )
    _write(
        root,
        "README.md",
        f"对象存储默认使用本地 **{readme_backend}**（`make dev-up` 自动启动容器），开箱即用。\n",
    )
    _write(
        root,
        "README_EN.md",
        f"Object storage defaults to local **{readme_backend}** (`make dev-up` "
        "starts the container), works out of the box.\n",
    )


def test_consistent_repo_passes(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    assert check_docs_consistency(tmp_path) == []


def test_schema_drift_is_rejected(tmp_path: Path) -> None:
    # Doc stays at v82 while the code moved to v83 — the bump-PR-forgets-
    # the-runbook case.
    _make_repo(tmp_path, schema_version=83, doc_schema_version=82)
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "states schema v82" in errors[0]
    assert "SCHEMA_VERSION = 83" in errors[0]


def test_backend_drift_is_rejected_in_both_readmes(tmp_path: Path) -> None:
    # decide script switched the default; both READMEs still name the old
    # backend — the #340 case.
    _make_repo(tmp_path, backend="minio", readme_backend="SeaweedFS")
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 2
    for error in errors:
        assert "**SeaweedFS**" in error
        assert "defaults to 'minio'" in error


def test_missing_schema_statement_is_rejected(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write(
        root=tmp_path,
        relative=SCHEMA_DOC,
        text="# runbook\n\n- 没有 version 句。\n- 默认端点 `http://127.0.0.1:8333`。\n",
    )
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "cannot find the '（目前 vNN）' schema-version statement" in errors[0]


def test_missing_readme_sentence_is_rejected(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write(root=tmp_path, relative="README.md", text="# Agent Legion\n")
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "README.md" in errors[0]
    assert "cannot find the 'local **Backend**' default-storage sentence" in errors[0]


def test_unparsable_schema_fails_closed(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write(root=tmp_path, relative=SCHEMA_FILE, text="SCHEMA_VERSION: 82\n")
    with pytest.raises(DocsConsistencySourceError):
        read_schema_version(tmp_path)
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "cannot find 'SCHEMA_VERSION = <int>'" in errors[0]
    # Error prose avoids "?" next to SQL keywords (rename/update/delete):
    # sql_placeholders.py scans scripts/ too and flags such strings.
    assert "?" not in errors[0]


def test_unparsable_compose_service_fails_closed(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write(
        root=tmp_path,
        relative=COMPOSE_FILE,
        text="services:\n  other:\n    image: x\n",
    )
    with pytest.raises(DocsConsistencySourceError):
        read_default_backend_port(tmp_path)
    # The backend name still parses; the compose failure surfaces as a
    # check error rather than a crash.
    assert read_default_backend(tmp_path) == "seaweedfs"
    errors = check_docs_consistency(tmp_path)
    assert any("cannot find the 'seaweedfs' service block" in error for error in errors)


def test_backend_port_is_read_from_compose(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    assert read_default_backend_port(tmp_path) == 8333


def test_port_drift_is_rejected(tmp_path: Path) -> None:
    # Compose republished the default backend on a new port; the runbook
    # still states the old loopback endpoint — a parsed-but-unasserted
    # port guards nothing (codex review on #744).
    _make_repo(tmp_path, doc_port=8333, compose_port=8443)
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "default endpoint states :8333" in errors[0]
    assert "publishes :8443" in errors[0]


def test_missing_endpoint_statement_is_rejected(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write(
        root=tmp_path,
        relative=SCHEMA_DOC,
        text="- 当前 schema 版本（目前 v82）。\n",
    )
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert "cannot find the '`http://127.0.0.1:<port>`' default-endpoint statement" in errors[0]


def test_partially_missing_sources_fail_closed(tmp_path: Path) -> None:
    # One source removed on the real repo (rename/move): the guard must
    # report it, not silently skip (codex review on #744). Only the
    # all-missing fixture case is a skip.
    _make_repo(tmp_path)
    (tmp_path / COMPOSE_FILE).unlink()
    errors = check_docs_consistency(tmp_path)
    assert len(errors) == 1
    assert f"source file missing: {COMPOSE_FILE}" in errors[0]


def test_no_sources_at_all_is_skipped(tmp_path: Path) -> None:
    # The minimal fixture repos of the other check_repository suites have
    # none of the sources — the guard is a no-op there.
    (tmp_path / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("# fixture\n", encoding="utf-8")
    assert check_docs_consistency(tmp_path) == []


def test_live_repo_is_consistent() -> None:
    """The real repo must pass its own guard — this is the assertion the
    CI job makes on every PR; keeping it as a unit case gives a local,
    marker-free reproduction of the gate."""
    import scripts.architecture.docs_consistency as module

    repo_root = Path(module.__file__).resolve().parents[2]
    assert check_docs_consistency(repo_root) == []
