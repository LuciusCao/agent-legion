"""Built-in authoring playbook served by the ``get_authoring_guide`` MCP tool.

Static text, versioned with the repo (decision: no external skill repo — the
guide must describe the platform the code actually implements). Every claim
below mirrors real behavior: workflow schema (server/app/workflows/loader.py),
publish validation (server/app/services/workflow_drafts.py), node-code
contract (server/app/services/node_codes.py), agent definitions
(server/app/agent_catalog.py), config schema subset
(server/app/config_schema.py), workspace-first publishing
(server/app/services/workflow_draft_publish.py). Update this text whenever
those behaviors change.
"""

from __future__ import annotations

AUTHORING_GUIDE = """\
# Agent Legion Workflow Authoring Guide

How to author a workflow on this platform through the agent-legion-studio MCP
tools. Everything you write is a DRAFT: a human reviews and publishes in
Studio. Nothing you do takes effect in production by itself.

## 1. Tool map (in the order you typically need them)

- `get_studio_context()` — which workspace this session is bound to and which
  node the human has selected. Call first; takes no workspace_id.
- `get_active_workflow(workspace_id)` — the live revision + full definition
  YAML. Answers `{"state": "empty", ...}` (HTTP 200) when the workspace has no
  published workflow yet: that is your signal to author from scratch, not an
  error.
- `validate_workflow(workspace_id, definition_yaml)` — the full publish
  validation set (structure + bindings). Persists nothing.
- `compare_workflow(workspace_id, definition_yaml)` — diff vs the active
  revision; with no published baseline it degrades to a full-draft preview
  (everything reported as added, `base_revision: null`).
- `save_node_code_draft(workspace_id, workflow_key, node_key, code, ...)` —
  draft Python source for a code node.
- `get_node_code(workspace_id, workflow_key, node_key)` — effective code plus
  any pending draft (origin: builtin | custom | none).
- `save_agent_definition_draft(agent_id, capability, runtime, skill, tools)` —
  draft Agent definition for an agent-backed capability.

There is NO tool to create workspaces or to publish anything, and no workflow
registry anymore (schema v50): a workflow is simply the DAG inside one
workspace. The human creates the workspace in Studio (blank canvas, or
initialized from the sample template) and publishes workflow revisions, node
code, and agent definitions.

## 2. From-scratch flow (empty workspace)

1. `get_studio_context` → learn the bound workspace; `get_active_workflow`
   → `state: "empty"` confirms there is nothing yet.
2. Pick the workflow key: a workspace with no published revision yet accepts
   any snake_case key — the first publish adopts the draft's `key` as the
   workspace default workflow key. If the workspace already has a default
   key, the draft's `key` MUST equal it, or compare/validate reject it.
3. Draft the definition YAML (section 3).
4. `validate_workflow` → fix every reported error. Then `compare_workflow`
   → preview the full shape. Repeat until clean.
5. For each code node, `save_node_code_draft` with `expected_capability` set
   (section 4). For each agent-backed capability without a published Agent,
   `save_agent_definition_draft` (section 5).
6. Present the draft YAML + validation result to the human and ask them to
   publish in Studio. Publish is never your job.

## 3. Workflow definition YAML

```yaml
key: my_workflow            # = workspace default_workflow_key, snake_case
label: 人类可读名称
schema_version: 2           # 2 recommended; 1 derives edges from `after`
intake:                     # optional; how jobs enter the workflow
  modes:
    direct_ids:
      label: 按条目批量
      input_field: item_ids
nodes:                      # mapping, declaration order = presentation order
  fetch_data:
    label: 拉取数据
    capability: fetch_data  # REQUIRED, non-empty; see section 4
    inputs: []              # artifact names this node consumes
    outputs: [data.json]    # artifact names this node produces
  report:
    label: 汇总
    capability: report
    inputs: [data.json]
    outputs: [report.md]
    terminal:               # optional: mark a terminal outcome
      outcome: done
    execution:              # optional, agent nodes: provider/model/thinking/prompt
      model: gpt-5.2
    config: {}              # optional per-node tunables (see section 5)
edges:                      # schema_version 2: explicit; optional `when`
  - {from: fetch_data, to: report}
  # - from: review
  #   to: publish
  #   when: {artifact: review.json, path: "$.approved", equals: true}
```

Hard rules enforced at parse/validate time:
- The DAG must be acyclic; `after`/`edges` must reference known nodes.
- Edge conditions: `when.path` must start with `$.`; `artifact`/`equals`
  required.
- Removed fields fail loudly: `runner`, `agent`, `resources` on nodes and
  `concurrency` at top level are rejected with migration messages. Nodes
  declare ONLY business capabilities — never runtimes, skills, or commands.

## 4. Capabilities and node kinds

A capability is a snake_case verb_noun (`fetch_data`, `review_questions`).
The node's kind is decided by how the capability resolves at publish
validation:

- AGENT node: exactly one published AgentDefinition exists for the
  capability. Zero or two published agents for one capability both fail
  validation.
- CODE node: every node without an Agent route runs on the implicit code
  pool (P-0.5); publish validation requires resolvable published node code
  (workspace version or global factory seed). Otherwise validation reports
  `no published node code for <workflow_key>.<node_key>` — publish the code
  with `save_node_code_draft` (skeleton draft + `expected_capability`) first.

`save_node_code_draft` code contract: the module must define a module-level
`run` function (syntax-checked, max 64 KB). Prefer `def run(ctx)` with the
node SDK (`workspace_libs/node_sdk.py`: `@entrypoint`, `NodeContext` for
artifact IO / service_config / checkpoint); the classic
`run(job, job_dir, runtime)` signature still works. Use
`workspace_libs/http_client.py` / `download.py` for network access (SSRF
guarded) — never raw socket code. Pass `expected_capability` when saving:
- Existing node: validated against the active revision — mismatch is a 400
  naming both capabilities.
- Node not in any published revision yet: only accepted WITH
  `expected_capability`, creating a skeleton draft ahead of the workflow
  draft that introduces the node. Without it you get 404.

## 5. Agent definitions and tunables

`save_agent_definition_draft` binds a capability to an implementation:
- `runtime`: one of `pi`, `openclaw`, `velites` (anything else is rejected).
- `skill`: relative skill path (`group/skill-name`); absolute paths and `..`
  are rejected.
- `tools`: allowlist, default `["read", "write", "bash"]`.
- Tunables: the Agent definition (or the workflow node's `config_schema:`
  block) declares a JSON-Schema subset: top-level `type: "object"` with
  `properties`/`required`; property types `string|integer|number|boolean`
  with optional `description`, `default`, `enum`, `minimum`, `maximum`, and
  `secret: true` for sensitive values (secrets never leave the server; nodes
  read them via `secret_ref`). `timeout_seconds`/`sandbox_network` are
  platform-reserved execution keys — never redeclare them in a
  `config_schema`; set them via node `config:` or workspace overrides.
  Values resolve schema defaults → node `config` → workspace override,
  frozen at job intake.
- Agent execution (`provider`/`model`/`thinking`) resolves node
  `execution.*` overrides → workspace defaults → validation error if unset.

## 6. Common errors and what to do

- `Draft workflow key '...' does not match workspace default workflow key
  '...'` — the workspace already has a key; re-emit the YAML with that key.
- `no published node code for ...` — publish the node code first
  (`save_node_code_draft` with `expected_capability`, then publish).
- `Agent capability X must resolve to exactly one published Agent` — draft
  (or ask the human to publish/archive) an Agent definition for X.
- `node code must define a module-level 'run' function` / `not valid Python`
  — fix the code before re-saving.
- 404 `Unknown workflow node` / `No active workflow revision` on
  save_node_code_draft — you forgot `expected_capability` for a new node.
- `HTTP 401` — token expired/revoked; ask the human to mint a new one.

Golden rule: validate first, compare second, present third — and let the
human publish.
"""
