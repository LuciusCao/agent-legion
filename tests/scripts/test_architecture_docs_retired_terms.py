"""Fixture tests for the docs retired-terms guard.

Same shape as ``test_architecture_sql_placeholders.py``: build a temporary
repo tree, point the checker at it, assert hit / exemption / whitelist /
configuration-error / index-reconciliation paths.
"""

from pathlib import Path

import pytest

from scripts.architecture.docs_retired_terms import (
    DocsRetiredTermsConfigurationError,
    RetiredTerm,
    check_docs_retired_terms,
    find_retired_term_hits,
    load_docs_retired_terms_config,
)
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


MINIMAL_CONFIG = """
terms:
  - pattern: '\\bopenclaw\\b'
    retired_in: '#75'
    note: runtime retired
  - pattern: 'config/skills\\.lock'
    retired_in: '#322'
    note: lock lives in DB
exemptions: []
"""


def make_repo(tmp_path: Path, *, config: str = MINIMAL_CONFIG) -> Path:
    """Minimal tree the checker can run against: config + index + one
    whitelisted doc + the neutral budget governance the repo-level
    check_repository also loads (not needed for the unit paths below, but
    keeps make_repo reusable if tests later call the full entry). The
    governance helper also writes a default docs-retired-terms.yaml, so
    the config write must come after it to win."""
    write_neutral_budget_governance(tmp_path)
    write(tmp_path / "config/architecture/docs-retired-terms.yaml", config)
    write_index(tmp_path)
    write(tmp_path / "README.md", "# readme\n")
    write(tmp_path / "docs/data-layout.md", "# data layout\n")
    return tmp_path


def write_index(
    tmp_path: Path,
    *,
    current: str | None = None,
    extra_current: str = "",
    historical: str = "| 归档 | [old.md](old.md) | x |\n",
) -> None:
    """Index fixture whose current-state table lists every whitelisted
    architecture doc by default, so reconciliation passes; individual
    tests override ``current``/``extra_current`` to break it on purpose."""
    if current is None:
        rows = "".join(
            f"| doc | [{name}]({name}) | x |\n"
            for name in (
                "backend.md",
                "frontend.md",
                "deployment.md",
                "project-structure.md",
                "local-quality-gates.md",
                "velites-harness.md",
                "velites-model-registry.md",
                "workspace-executor-evidence-matrix.md",
                "node-sdk-and-worker-execution-design.md",
                "materials-and-runs-design.md",
            )
        )
        current = rows
    write(
        tmp_path / "docs/architecture/README.md",
        "# index\n\n## 现行文档（描述当前系统状态）\n\n"
        + current
        + extra_current
        + "\n## 历史设计记录（时点快照，仅供溯源）\n\n"
        + historical
        + "\n| 归档2 | [proposal](docs-governance-proposal.md) | x |\n",
    )


# ---------------------------------------------------------------------------
# Hit / exemption semantics
# ---------------------------------------------------------------------------


def test_hits_current_behavior_mention() -> None:
    terms = (RetiredTerm(pattern=r"\bopenclaw\b", retired_in="#75"),)
    hits = find_retired_term_hits(["runs agents via openclaw"], terms)
    assert hits == [(1, terms[0])]


def test_exempts_retirement_phrase_in_same_line() -> None:
    terms = (RetiredTerm(pattern=r"\bopenclaw\b", retired_in="#75"),)
    hits = find_retired_term_hits(["the openclaw runtime was retired in #75"], terms)
    assert hits == []


def test_exempts_retirement_phrase_in_neighbor_line() -> None:
    terms = (RetiredTerm(pattern=r"\bopenclaw\b", retired_in="#75"),)
    hits = find_retired_term_hits(["runs agents via openclaw", "openclaw was retired (#75)"], terms)
    assert hits == []


def test_live_code_paths_are_not_patterns() -> None:
    # The v1 pattern set must not match executor module paths — the whole
    # reason "executor" alone is not a pattern.
    terms = (RetiredTerm(pattern=r"\bexecutor (definition|binding|allocation)s?\b"),)
    hits = find_retired_term_hits(
        ["capacity via server/app/executors/leases.py and worker/executor.py"], terms
    )
    assert hits == []


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write(path, "terms:\n  - pattern: x\nexemptions: []\nextra: 1\n")
    with pytest.raises(DocsRetiredTermsConfigurationError):
        load_docs_retired_terms_config(path)


def test_config_rejects_empty_terms(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write(path, "terms: []\nexemptions: []\n")
    with pytest.raises(DocsRetiredTermsConfigurationError):
        load_docs_retired_terms_config(path)


def test_config_rejects_duplicate_patterns(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write(path, "terms:\n  - pattern: x\n  - pattern: x\nexemptions: []\n")
    with pytest.raises(DocsRetiredTermsConfigurationError):
        load_docs_retired_terms_config(path)


def test_config_rejects_invalid_regex(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write(path, "terms:\n  - pattern: '[unclosed'\nexemptions: []\n")
    with pytest.raises(DocsRetiredTermsConfigurationError):
        load_docs_retired_terms_config(path)


def test_config_rejects_bad_exemption_shape(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write(path, "terms:\n  - pattern: x\nexemptions:\n  - path: a.md\n")
    with pytest.raises(DocsRetiredTermsConfigurationError):
        load_docs_retired_terms_config(path)


def test_missing_config_is_reported_as_error(tmp_path: Path) -> None:
    write_index(tmp_path)
    errors = check_docs_retired_terms(tmp_path)
    assert any("configuration" in error for error in errors)


# ---------------------------------------------------------------------------
# Whitelist behavior
# ---------------------------------------------------------------------------


def test_violation_in_whitelisted_doc_is_error(tmp_path: Path) -> None:
    make_repo(tmp_path)
    write(tmp_path / "docs/data-layout.md", "# layout\n\nuses openclaw\n")
    errors = check_docs_retired_terms(tmp_path)
    assert any("docs/data-layout.md:3" in error and "openclaw" in error for error in errors)


def test_retirement_phrase_in_whitelisted_doc_passes(tmp_path: Path) -> None:
    make_repo(tmp_path)
    write(tmp_path / "docs/data-layout.md", "# layout\n\nopenclaw runtime 已退役 (#75)\n")
    errors = check_docs_retired_terms(tmp_path)
    assert not any("openclaw" in error for error in errors)


def test_changelog_is_not_scanned(tmp_path: Path) -> None:
    make_repo(tmp_path)
    # CHANGELOG describes what changed at the time; retired items appear
    # there legitimately and must not be flagged.
    write(
        tmp_path / "CHANGELOG.md",
        "# changelog\n\n- openclaw runtime removed entirely\n- config/skills.lock retired\n",
    )
    errors = check_docs_retired_terms(tmp_path)
    assert not any("CHANGELOG" in error for error in errors)


def test_exemption_entry_suppresses_hit(tmp_path: Path) -> None:
    config = """
terms:
  - pattern: '\\bopenclaw\\b'
    retired_in: '#75'
exemptions:
  - path: docs/data-layout.md
    term: '\\bopenclaw\\b'
    reason: quoted in a table for historical contrast
    remove_when: the table is rewritten without the term
"""
    make_repo(tmp_path, config=config)
    write(tmp_path / "docs/data-layout.md", "# layout\n\nuses openclaw\n")
    errors = check_docs_retired_terms(tmp_path)
    assert not any("openclaw" in error for error in errors)


# ---------------------------------------------------------------------------
# Index reconciliation
# ---------------------------------------------------------------------------


def test_whitelist_entry_missing_from_index_is_error(tmp_path: Path) -> None:
    make_repo(tmp_path)
    # backend.md is whitelisted in _CURRENT_DOCS but the fixture index only
    # lists it via write_index's default row — overwrite the index without it.
    write_index(tmp_path, current="| 其他 | [elsewhere.md](elsewhere.md) | x |\n")
    errors = check_docs_retired_terms(tmp_path)
    assert any("whitelist" in error and "backend.md" in error for error in errors)


def test_indexed_current_doc_missing_from_whitelist_is_error(tmp_path: Path) -> None:
    make_repo(tmp_path)
    write_index(tmp_path, extra_current="| 新 | [new-doc.md](new-doc.md) | x |\n")
    errors = check_docs_retired_terms(tmp_path)
    assert any("missing from the docs-retired-terms whitelist" in error for error in errors)


def test_historical_section_entries_do_not_require_whitelist(tmp_path: Path) -> None:
    make_repo(tmp_path)
    # old.md sits in the historical table only; it must not demand a
    # whitelist entry, and the proposal doc link is explicitly exempt.
    errors = check_docs_retired_terms(tmp_path)
    assert not any("old.md" in error for error in errors)
    assert not any("docs-governance-proposal" in error for error in errors)


def test_missing_index_file_skips_reconciliation(tmp_path: Path) -> None:
    # Minimal fixture trees (the other check_repository suites) have no
    # docs/architecture/README.md; the terminology scan still runs and
    # flags violations, the index reconciliation is simply skipped.
    write(tmp_path / "config/architecture/docs-retired-terms.yaml", MINIMAL_CONFIG)
    write(tmp_path / "docs/data-layout.md", "# layout\n\nuses openclaw\n")
    errors = check_docs_retired_terms(tmp_path)
    assert any("openclaw" in error for error in errors)
    assert not any("index" in error for error in errors)
