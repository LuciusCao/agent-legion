"""Built-in authoring playbook served by the ``get_authoring_guide`` MCP tool.

Static text, versioned with the repo (decision: no external skill repo — the
guide must describe the platform the code actually implements). The text
lives in the sibling ``authoring_guide.md`` resource (file budget: the
playbook outgrew this module's line ceiling). Every claim there mirrors real
behavior: workflow schema (server/app/workflows/loader.py),
publish validation (server/app/services/workflow_drafts.py), node-code
contract (server/app/services/node_codes.py), agent definitions
(server/app/agent_catalog/definition.py), config schema subset
(server/app/config_schema.py), workspace-first publishing
(server/app/services/workflow_draft_publish.py), skill tools
(server/app/services/skill_editing.py). Update this text whenever
those behaviors change.

#660: ``guide_section`` exposes the playbook per ``## N.`` chapter so agents
can pull one chapter instead of the full text. The split happens once at
import; a chapter-count drift (renumbered/retitled headings) fails fast here
rather than returning a wrong chapter to an agent.
"""

from __future__ import annotations

import re
from pathlib import Path

AUTHORING_GUIDE = Path(__file__).with_name("authoring_guide.md").read_text(encoding="utf-8")

# Chapter keys in heading order (authoring_guide.md ## 1. … ## 7.).
SECTION_KEYS = ("tool-map", "flow", "yaml", "capabilities", "agents", "skills", "errors")


def _split_sections() -> dict[str, str]:
    parts = re.split(r"(?m)^## \d+\. ", AUTHORING_GUIDE)
    chapters = parts[1:]  # parts[0] 是全文引言，只属于默认全文返回
    if len(chapters) != len(SECTION_KEYS):
        raise RuntimeError(
            f"authoring_guide.md chapter count {len(chapters)} != {len(SECTION_KEYS)}"
        )
    return {key: f"## {body.strip()}" for key, body in zip(SECTION_KEYS, chapters, strict=True)}


_SECTIONS = _split_sections()


def guide_section(section: str | None) -> str:
    """Return the full guide (section None) or one chapter by key."""
    if section is None:
        return AUTHORING_GUIDE
    text = _SECTIONS.get(section.strip().lower())
    if text is None:
        valid = ", ".join(SECTION_KEYS)
        return f"Unknown guide section {section!r}. Valid sections: {valid}. Omit section for the full guide."
    return text
