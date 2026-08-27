"""Built-in authoring playbook served by the ``get_authoring_guide`` MCP tool.

Static text, versioned with the repo (decision: no external skill repo — the
guide must describe the platform the code actually implements). The text
lives in the sibling ``authoring_guide.md`` resource (file budget: the
playbook outgrew this module's line ceiling). Every claim there mirrors real
behavior: workflow schema (server/app/workflows/loader.py),
publish validation (server/app/services/workflow_drafts.py), node-code
contract (server/app/services/node_codes.py), agent definitions
(server/app/agent_catalog.py), config schema subset
(server/app/config_schema.py), workspace-first publishing
(server/app/services/workflow_draft_publish.py). Update this text whenever
those behaviors change.
"""

from __future__ import annotations

from pathlib import Path

AUTHORING_GUIDE = Path(__file__).with_name("authoring_guide.md").read_text(encoding="utf-8")
