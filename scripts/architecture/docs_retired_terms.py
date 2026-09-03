"""Guard: retired terminology must not reappear as current behavior in
current-state docs.

Every release train retires concepts (``#360`` alone retired the executor
definition/binding model, the openclaw runtime, the skill source registry,
the worker yaml seed, and the global register token), and "the retirement
was announced" does not mean "every current-state doc was updated" — the
``#360`` review caught the architecture README overview diagram still
describing pipeline nodes and external skills weeks after both were gone.
``generate_architecture --check`` only protects AUTO-GENERATED sections;
prose and diagrams were the blind spot this check closes.

Semantics (see docs/architecture/docs-governance-proposal.md):

- Scan only the ``_CURRENT_DOCS`` whitelist; point-in-time snapshots
  (risk reviews, PoC reports, ``docs/reviews/``) are exempt by design.
- A hit whose surrounding context carries a retirement phrase
  (``已退役`` / ``retired`` / …) is a legal historical mention — same
  spirit as ``broad_except_audit.py``'s audit-marker exemption.
- CHANGELOG is NOT whitelisted: its versioned sections describe what
  changed *at the time*, so retired items legitimately appear there.
- Patterns must target conceptual phrases, never live code paths
  (``executors/`` package and ``worker/executor.py`` are alive).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

__test__ = False

CONFIG_RELATIVE_PATH = "config/architecture/docs-retired-terms.yaml"
INDEX_RELATIVE_PATH = "docs/architecture/README.md"

# Must stay in sync with the "现行文档" table in docs/architecture/README.md
# (the index reconciliation below enforces the sync).
_CURRENT_DOCS = (
    "README.md",
    "README_EN.md",
    "AGENTS.md",
    "docs/README.md",
    "docs/architecture/README.md",
    "docs/architecture/backend.md",
    "docs/architecture/frontend.md",
    "docs/architecture/deployment.md",
    "docs/architecture/project-structure.md",
    "docs/architecture/local-quality-gates.md",
    "docs/architecture/velites-harness.md",
    "docs/architecture/velites-model-registry.md",
    "docs/architecture/workspace-executor-evidence-matrix.md",
    "docs/architecture/node-sdk-and-worker-execution-design.md",
    "docs/architecture/materials-and-runs-design.md",
    "docs/agent-worker-deployment.md",
    "docs/data-layout.md",
    "docs/materials-storage-deployment.md",
    "docs/postgresql-runbook.md",
    "docs/remote-execution-runbook.md",
    "docs/studio-agent-mcp.md",
    "scripts/README.md",
    "examples/README.md",
)

# Files under docs/ that read like current-state docs but are not: governance
# proposals quote retired terms (they must NAME the concepts they retire);
# the time-point snapshot zone is wholesale exempt.
_DOC_EXEMPT_PREFIXES = ("docs/reviews/",)
_LEGACY_CONCEPTS_PROPOSAL = "docs/architecture/instance-settings-legacy-concepts-governance.md"
# Both entries are governance proposals: they NAME the retired concepts they
# propose to retire, so term hits inside them are quotes, not behavior.
_DOC_EXEMPT_FILES = {"docs/architecture/docs-governance-proposal.md", _LEGACY_CONCEPTS_PROPOSAL}

# Retirement-phrase context: a hit inside such a sentence is describing the
# retirement itself. Known blind spot (recorded in the proposal §2.1): the
# phrase may sit in the same sentence while modifying something else.
_RETIREMENT_PHRASE = re.compile(
    r"已退役|已随|退役|不再|已删除|已移除|改用|历史|遗留|legacy|retired|removed|replaced|no longer|superseded",
    re.IGNORECASE,
)

# Context window: the hit's own line plus one neighbor line on each side.
_CONTEXT_RADIUS = 1


class DocsRetiredTermsConfigurationError(ValueError):
    """Malformed config/architecture/docs-retired-terms.yaml."""


@dataclass(frozen=True)
class RetiredTerm:
    pattern: str
    note: str = ""
    retired_in: str = ""

    @property
    def regex(self) -> re.Pattern[str]:
        return compile_term_pattern(self.pattern)


def compile_term_pattern(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise DocsRetiredTermsConfigurationError(f"invalid regex {pattern!r}: {exc}") from exc


@dataclass(frozen=True)
class DocsRetiredTermsConfig:
    terms: tuple[RetiredTerm, ...] = ()
    # Per-file escape hatch, same shape as architecture-exemptions.yaml:
    # {path, term, reason, remove_when}. Kept empty in v1.
    exemptions: tuple[dict[str, str], ...] = field(default_factory=tuple)


def load_docs_retired_terms_config(path: Path) -> DocsRetiredTermsConfig:
    """Strict loader: unknown fields, non-string patterns, and empty
    pattern lists are configuration errors, not silent no-ops."""
    if not path.is_file():
        raise DocsRetiredTermsConfigurationError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DocsRetiredTermsConfigurationError(f"malformed YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DocsRetiredTermsConfigurationError(
            f"config root must be a mapping, got {type(raw).__name__}"
        )
    if set(raw) != {"terms", "exemptions"}:
        extra = set(raw) - {"terms", "exemptions"}
        missing = {"terms", "exemptions"} - set(raw)
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        if extra:
            parts.append(f"unknown fields: {sorted(extra)}")
        raise DocsRetiredTermsConfigurationError(f"invalid config structure; {'; '.join(parts)}")
    raw_terms = raw["terms"]
    if not isinstance(raw_terms, list) or not raw_terms:
        raise DocsRetiredTermsConfigurationError("terms must be a non-empty list")
    terms: list[RetiredTerm] = []
    seen: set[str] = set()
    for entry in raw_terms:
        if not isinstance(entry, dict):
            raise DocsRetiredTermsConfigurationError("each term must be a mapping")
        if not {"pattern"} <= set(entry) or not set(entry) <= {
            "pattern",
            "note",
            "retired_in",
        }:
            raise DocsRetiredTermsConfigurationError(
                f"term keys must be a subset of pattern/note/retired_in, got {sorted(entry)}"
            )
        pattern = entry["pattern"]
        if not isinstance(pattern, str) or not pattern:
            raise DocsRetiredTermsConfigurationError("term pattern must be a non-empty string")
        if pattern in seen:
            raise DocsRetiredTermsConfigurationError(f"duplicate pattern: {pattern}")
        seen.add(pattern)
        # Compile eagerly so a malformed pattern is a load-time
        # configuration error, not a check-time crash.
        compile_term_pattern(pattern)
        terms.append(
            RetiredTerm(
                pattern=pattern,
                note=str(entry.get("note", "")),
                retired_in=str(entry.get("retired_in", "")),
            )
        )
    raw_exemptions = raw["exemptions"]
    if not isinstance(raw_exemptions, list):
        raise DocsRetiredTermsConfigurationError("exemptions must be a list")
    exemptions: list[dict[str, str]] = []
    for entry in raw_exemptions:
        if not isinstance(entry, dict) or set(entry) != {"path", "term", "reason", "remove_when"}:
            raise DocsRetiredTermsConfigurationError(
                f"exemption keys must be exactly path/term/reason/remove_when, got {entry!r}"
            )
        if not all(isinstance(value, str) and value for value in entry.values()):
            raise DocsRetiredTermsConfigurationError(
                f"exemption values must be non-empty strings, got {entry!r}"
            )
        exemptions.append(dict(entry))
    return DocsRetiredTermsConfig(
        terms=tuple(terms),
        exemptions=tuple(exemptions),
    )


def _exemption_pairs(config: DocsRetiredTermsConfig) -> set[tuple[str, str]]:
    return {(entry["path"], entry["term"]) for entry in config.exemptions}


def _is_exempt_doc(relative_path: str) -> bool:
    return relative_path in _DOC_EXEMPT_FILES or relative_path.startswith(_DOC_EXEMPT_PREFIXES)


def find_retired_term_hits(
    lines: list[str], terms: tuple[RetiredTerm, ...]
) -> list[tuple[int, RetiredTerm]]:
    """1-based (line, term) hits whose context window carries no
    retirement phrase."""
    hits: list[tuple[int, RetiredTerm]] = []
    for lineno in range(1, len(lines) + 1):
        window = "\n".join(lines[max(0, lineno - 1 - _CONTEXT_RADIUS) : lineno + _CONTEXT_RADIUS])
        for term in terms:
            if term.regex.search(lines[lineno - 1]) and not _RETIREMENT_PHRASE.search(window):
                hits.append((lineno, term))
    return hits


def check_docs_retired_terms(root: Path) -> list[str]:
    """Reject retired terminology presented as current behavior."""
    root = root.resolve()
    try:
        config = load_docs_retired_terms_config(root / CONFIG_RELATIVE_PATH)
    except DocsRetiredTermsConfigurationError as exc:
        return [f"docs retired terms configuration: {exc}"]

    exempt_pairs = _exemption_pairs(config)
    errors: list[str] = []
    for relative_path in _CURRENT_DOCS:
        path = root / relative_path
        if not path.is_file():
            # Missing whitelist docs are the index reconciliation's problem
            # (below), not a terminology hit.
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, term in find_retired_term_hits(lines, config.terms):
            if (relative_path, term.pattern) in exempt_pairs:
                continue
            errors.append(
                f"{relative_path}:{lineno}: retired terminology '{term.pattern}' "
                "presented as current behavior; update the doc or add an exemption "
                f"with remove_when ({term.retired_in or 'see config note'})"
            )
    errors.extend(_check_index_reconciliation(root))
    return sorted(errors)


def _check_index_reconciliation(root: Path) -> list[str]:
    """Every whitelisted architecture doc must be registered in the
    "现行文档" table of docs/architecture/README.md, and every table entry
    must have a whitelist counterpart — the sync rule the proposal
    fixed after codex review #364.

    The reconciliation requires the index file to exist; a tree without
    it (e.g. the minimal fixture repos of the other check_repository
    suites) skips reconciliation but still gets the terminology scan —
    the yaml config, not the index, is this check's subject proper."""
    index_path = root / INDEX_RELATIVE_PATH
    if not index_path.is_file():
        return []
    text = index_path.read_text(encoding="utf-8", errors="replace")

    current_section = _extract_section(text, "现行文档")
    if current_section is None:
        return [f"{INDEX_RELATIVE_PATH}: missing '## 现行文档' section"]
    # Index links are relative to the index file ("backend.md"); whitelist
    # entries are repo-relative — normalize both to repo-relative.
    indexed = {
        _normalize_repo_relative(link, index_path.parent, root)
        for link in re.findall(r"\]\(([^)]+\.md)\)", current_section)
    }
    # The historical snapshot table lives after the current-state table;
    # links only the first section mentions are current-state filings.
    historical_section = _extract_section(text, "历史设计记录") or ""
    historical = {
        _normalize_repo_relative(link, index_path.parent, root)
        for link in re.findall(r"\]\(([^)]+\.md)\)", historical_section)
    }
    indexed -= historical

    whitelisted_arch = {
        path
        for path in _CURRENT_DOCS
        if path.startswith("docs/architecture/") and path != INDEX_RELATIVE_PATH
    }
    errors: list[str] = []
    for path in sorted(whitelisted_arch - indexed):
        errors.append(
            f"{INDEX_RELATIVE_PATH}: '{path}' is in the docs-retired-terms whitelist "
            "but not in the '现行文档' index table (or wrongly filed under "
            "'历史设计记录')"
        )
    for resolved in sorted(indexed - whitelisted_arch):
        if _is_exempt_doc(resolved):
            continue
        errors.append(
            f"{INDEX_RELATIVE_PATH}: '{resolved}' is indexed as a current-state doc "
            "but missing from the docs-retired-terms whitelist "
            "(_CURRENT_DOCS in scripts/architecture/docs_retired_terms.py)"
        )
    return errors


def _normalize_repo_relative(link: str, base: Path, root: Path) -> str:
    """Resolve an index-relative markdown link to a repo-relative posix
    path. Links escaping the repo (rare, e.g. ../../AGENTS.md) resolve
    fine; broken links are preserved as-is so they surface as mismatches."""
    resolved = (base / link).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return link


def _extract_section(text: str, heading_prefix: str) -> str | None:
    """Lines between a ``##`` heading starting with ``heading_prefix`` and
    the next same-level heading. Headings carry parenthetical suffixes
    ("## 现行文档（描述当前系统状态）"), so match on the prefix."""
    lines = text.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.startswith("## ") and line[3:].startswith(heading_prefix):
            start = idx + 1
            break
    if start is None:
        return None
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected)


if __name__ == "__main__":
    # Standalone entry for the CI docs-terms job (which runs this module in a
    # --no-project env with only pyyaml) and for local debugging; the full
    # gate path stays check_repository.
    repo_root = Path(__file__).resolve().parents[2]
    failures = check_docs_retired_terms(repo_root)
    for failure in failures:
        print(f"ERROR: {failure}")
    raise SystemExit(1 if failures else 0)
