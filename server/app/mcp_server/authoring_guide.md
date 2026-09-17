# Agent Legion Workflow Authoring Guide

How to author a workflow on this platform through the agent-legion-studio MCP
tools. Everything you write is a DRAFT: a human reviews and publishes in
Studio. Nothing you do takes effect in production by itself.

## 1. Tool map (in the order you typically need them)

- `get_studio_context()` — which workspace this session is bound to, which
  node the human has selected, and the canvas' current unpublished workflow
  draft YAML (null until the human's Studio pushes it). Call first; takes no
  workspace_id. Registered only when a chat session is bound (Studio chat;
  external self-service setups do not have this tool).
- `get_active_workflow(workspace_id)` — the live revision + full definition
  YAML. Answers `{"state": "empty", ...}` (HTTP 200) when the workspace has no
  published workflow yet: that is your signal to author from scratch, not an
  error.
- `get_workflow_draft(workspace_id)` — the workspace's unpublished Studio
  draft (the SAME draft the canvas autosaves; NOT the active revision).
  `{"definition_yaml": ..., "updated_at": ...}`; both null when no draft was
  ever saved. The `updated_at` is your CAS token for `save_workflow_draft`.
- `save_workflow_draft(workspace_id, definition_yaml, expected_updated_at)` —
  write the full definition YAML into the Studio draft; the human's canvas and
  YAML editor pick it up. CAS: `expected_updated_at` must be the `updated_at`
  from your last read (or the literal `never-saved` when none existed). A
  stale token returns HTTP 409 with the current draft embedded
  (`current_draft.definition_yaml` / `current_draft.updated_at`) — rebase your
  changes onto that draft and retry with its timestamp; never retry the old
  one. Draft only: publishing stays with `request_workflow_publish`.
- `validate_workflow(workspace_id, definition_yaml)` — the full publish
  validation set (structure + bindings). Persists nothing.
- `compare_workflow(workspace_id, definition_yaml)` — diff vs the active
  revision; with no published baseline it degrades to a full-draft preview
  (everything reported as added, `base_revision: null`, and the returned
  `draft_workflow.version` is the synthetic placeholder `0`, not a real
  revision number).
- `request_workflow_publish(workspace_id)` — ask the human to publish the
  workspace's unpublished draft. NEVER publishes by itself: it parks a
  pending request; the human sees the publish review dialog in Studio and
  confirms or cancels. The draft must already pass full validation (a draft
  with errors returns HTTP 409 and creates no request), and the request
  binds the CURRENT server draft — if the draft changes afterwards, the
  human's confirm refuses with 409 and you must re-request. A 409 during
  another request's confirm window means: wait and re-request once it
  resolves. After calling, tell the human to review the dialog.
- `get_publish_request_status(request_id)` — poll the outcome of a publish
  request: `pending` until the human decides; `confirming` (the human
  pressed confirm; the publish is in flight); `confirmed`
  (`result_revision_id` set only when the publish created a new revision —
  a runtime-only config update keeps it null), `rejected`, `superseded`
  (a newer request of yours or a manual human publish displaced it), or
  `expired` (nobody answered within the TTL). `superseded` by a manual
  publish still means the draft went live — check the active revision.
- `save_node_code_draft(workspace_id, node_key, code, ...)` —
  draft Python source for a code node.
- `get_node_code(workspace_id, node_key)` — effective code plus
  any pending draft (origin: builtin | custom | none). Nodes that only exist
  in your not-yet-published draft are readable too (a skeleton draft you saved
  reads back; otherwise origin `none`); only start nodes 404.
- `get_agent_definitions(workspace_id)` — the workspace's Agent definitions:
  the latest version per agent (a pending draft beats the published row) with
  ALL fields — capability, runtime, skill, tools, requires_labels,
  config_schema — plus version metadata (version, status, definition_hash,
  created_by, created_at, published_at). Read this BEFORE drafting agent or
  workflow changes so capability bindings build on what exists.
- `create_agent_definition(workspace_id, capability, runtime, skill,
  tools?, requires_labels?, config_schema?)` — start a NEW Agent definition
  draft for a capability that has no Agent yet (any status): the agent_id
  derives from the capability, and a colliding capability returns HTTP 409
  naming the existing Agent — edit that one with
  `save_agent_definition_draft` instead. Same payload fields as the save
  (section 5). Draft only; a human publishes it in Studio.
- `get_runtime_models(workspace_id)` — the workspace's available
  `{runtime: {provider: [models]}}` view aggregated from its ONLINE workers'
  declarations. Discovery only: provider/model declarations are worker-owned
  and can never be edited through these tools; use the view to pick sensible
  node `execution.*` values (a typed value corresponds to a worker that can
  actually claim the execution).
- `get_agent_runtimes(workspace_id)` — the runtime catalog: each runtime
  (pi, velites) and its agent tool catalog — tool names, tiers (`default`
  preselected / `opt-in` explicit / `forced` harness-enforced with an
  activation condition) and parameters. The catalog is code-defined and
  static; the only editable tool surface is the `tools` selection inside an
  Agent definition draft.
- `save_agent_definition_draft(workspace_id, agent_id, capability, runtime,
  skill, tools?, requires_labels?, config_schema?)` — draft an EXISTING
  Agent definition for an agent-backed capability (section 5). FULL-PAYLOAD
  semantics: an omitted optional field RESETS to its default (tools →
  catalog default tier, requires_labels/config_schema → {}) — call
  `get_agent_definitions` first and echo back every value you want kept.
- `get_node_prompt(workspace_id, node_key, definition_yaml?)` — the effective
  run prompt of an agent node: fixed platform envelope + node instructions
  (auto-assembled default, or the custom `execution.prompt` when set). Read
  it before writing a custom prompt.
- `save_node_prompt(workspace_id, node_key, prompt)` — write a custom
  `execution.prompt` for one node into the workspace's unpublished draft
  YAML; an empty string clears it back to the auto-assembled default.
- `get_skill(workspace_id, skill_key, ref=None)` — a skill's configured ref,
  repo tags
  (latest first), and text files: the LOCKED commit's content when the lock
  pins one, else the working tree; `ref` previews one git tag without moving
  the lock.
- `validate_skill(workspace_id, skill_key)` — the runtime skill contract as a
  structured
  error list. Persists nothing.
- `save_skill_version(workspace_id, skill_key, files, new_tag, message)` —
  commit + tag a
  new version in the skill's LOCAL source repo (section 6). Lock untouched.
- `create_skill(workspace_id, skill_name, files, new_tag, message)` — create
  a BRAND-NEW skill repo at `<skills root>/<workspace_id>/<skill_name>`
  (#633, workspace-scoped): the files must carry the full contract set of
  four files (section 6) and everything is validated before anything is
  written; on
  success the initial commit is tagged `new_tag`. Existing dir → 409. After
  the create, iterate with `validate_skill` / `save_skill_version`. Lock
  untouched.
- `get_shared_materials(workspace_id)` — the workspace's shared skill
  materials (`_shared/map.json` + `references/` + `scripts/`); a workspace
  without `_shared` returns the structured empty state `{"map": null,
  "files": []}` — that is your signal to author them (section 6.1).
- `save_shared_materials(workspace_id, files)` — author those shared
  materials (section 6.1). Draft-only: `_shared` is not a git repo; the
  audit trail is the commits the sync lands in each mapped skill.

There is NO tool to create workspaces, and no workflow registry anymore
(schema v50): a workflow is simply the DAG inside one workspace. The human
creates the workspace in Studio (blank canvas, or initialized from the sample
template) and owns every publish decision: your `request_workflow_publish`
only asks — the human confirms in the review dialog (the publish, node code
publish, agent definition publish, and skill release actions stay human-only).

## 2. From-scratch flow (empty workspace)

0. Outline confirmation: present the human an outline of the workflow — the
   node list, each node's responsibility and capability, the artifact flow
   (inputs/outputs), and the edge directions — and explicitly ask them to
   confirm it. Draft no YAML until the human confirms the outline. (Small
   changes to an existing workflow — tuning config, adding or editing a
   single node, editing a prompt — skip this step.)
1. `get_studio_context` → learn the bound workspace; `get_active_workflow`
   → `state: "empty"` confirms there is nothing yet.
2. Pick the workflow key: a workspace with no published revision yet accepts
   any snake_case key — the first publish adopts the draft's `key` as the
   workspace default workflow key. If the workspace already has a default
   key, the draft's `key` MUST equal it, or compare/validate reject it.
3. Draft the definition YAML (section 3).
4. `validate_workflow` → fix every reported error. Then `compare_workflow`
   → preview the full shape. Repeat until clean.
5. `save_workflow_draft` → persist the validated YAML as the Studio draft.
   The CAS token (`expected_updated_at`) comes from `get_workflow_draft`'s
   `updated_at`, from `get_studio_context`'s `draft_updated_at` (same value —
   the draft row's updated_at), or the literal `never-saved`. Never invent a
   timestamp. On a 409, rebase onto the response's `current_draft` and retry
   with its `updated_at` — the human may have edited concurrently.
6. For each code node, `save_node_code_draft` with `expected_capability` set
   (section 4). For each agent-backed capability without a published Agent,
   `create_agent_definition` (new capability) or `save_agent_definition_draft`
   (edit an existing Agent) (section 5).
7. Present the change summary to the human, then call
   `request_workflow_publish` — the publish review dialog pops in Studio with
   the same compare data. Poll `get_publish_request_status`: confirmed means
   live, rejected/expired means revise the draft and re-request, superseded
   means the request was displaced (a newer one of yours, or the human
   published manually — check the active revision to tell which). The final
   publish decision is never yours.

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
      model: gpt-5.2        # prompt: empty = auto-assembled default instructions;
                            # non-empty = replaces the default wholesale
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
  pool (P-0.5); publish validation requires a published workspace node-code
  version. Otherwise validation reports
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

Agent-definition authoring loop (read → discover → draft):
1. `get_agent_definitions(workspace_id)` — what already exists: latest
   version per agent with every field. A pending draft beats the published
   row, so the list shows exactly what the next publish would ship.
2. Discover the surroundings you canNOT edit:
   - `get_agent_runtimes(workspace_id)` — the per-runtime tool catalog
     (code-defined, static): which tools exist for pi/velites, their tiers
     and activation conditions. Pick `tools` values from THIS catalog.
   - `get_runtime_models(workspace_id)` — the worker-declared
     runtime → provider → models view, so node `execution.model` values you
     draft correspond to models an online worker can actually claim.
3. Start a new Agent with `create_agent_definition(...)` when the capability
   has no Agent yet (409 = the capability is taken — edit that Agent
   instead), or edit an existing one with `save_agent_definition_draft(...)`.
   Either way a human publishes it in Studio; publishing/archiving is never
   yours.

`create_agent_definition` / `save_agent_definition_draft` bind a capability
to an implementation:
- `runtime`: one of `pi`, `velites` (anything else is rejected).
- `skill`: relative skill path (`group/skill-name`); absolute paths and `..`
  are rejected.
- `tools`: allowlist, default `["read", "write", "bash"]`.
- `requires_labels`: worker labels the agent requires
  (e.g. `{"gpu": "a100"}`) — only workers carrying every label can claim it.
- `config_schema`: tunables as a JSON-Schema subset (below).
- Tunables: the Agent definition (or the workflow node's `config_schema:`
  block) declares a JSON-Schema subset: top-level `type: "object"` with
  `properties`/`required`; property types `string|integer|number|boolean`
  with optional `description`, `default`, `enum`, `minimum`, `maximum`, and
  `secret: true` for sensitive values (secrets never leave the server; nodes
  read them via `secret_ref`). `timeout_seconds`/`sandbox_network` are
  platform-reserved execution keys — never redeclare them in a
  `config_schema`; set them via node `config:` or workspace overrides.
  Values resolve schema defaults → node `config` → workspace override,
  frozen at job intake; a property marked `runtime_mutable: true` (run
  switches like `dry_run`) opts out of the freeze and is re-resolved
  against the live workspace override at every dispatch
  (CONFIG-RUNTIME-MUTABLE-001).
- Node config `connection` keys reference instance-level external service
  connections (external APIs such as TTS or CMS; the boundary is
  SECURITY-EXTERNAL-CONNECTION-001). Those are admin-only and live in
  Admin → Global Settings → External Service Connections
  (/admin/settings#connections); no tool on this server reads, lists, or
  modifies them. When a workflow needs one, reference an existing
  connection key in the draft, or tell the human an administrator must
  configure it there — never claim external service connections are
  configured in Studio or in a workflow.
- Agent execution (`provider`/`model`/`thinking`) resolves node
  `execution.*` overrides → workflow top-level `execution:` defaults →
  validation error if unset.
- Node prompt (`execution.prompt`): the run prompt is a fixed platform
  envelope (job/skill paths, declared inputs/outputs, output discipline)
  plus one node-instructions section. Empty `execution.prompt` means the
  platform auto-assembles that section from the node's label, capability,
  bound skill, and declared IO; a non-empty value REPLACES the default
  wholesale — it is not appended. Preview with `get_node_prompt`, edit the
  draft with `save_node_prompt` (empty string clears back to the default).

### 5.1 Configuring execution (provider/model/thinking)

An agent node's `execution:` block carries `provider`, `model`, `thinking`
(the only keys; `prompt`/`prompt_mode` are node-level extras — see §5). The
effective value resolves at definition-load time:

- node `execution:` override first; empty keys fall through to
- the workflow's top-level `execution:` default block, which the loader
  merges into every non-start node (workspace-level defaults retired at
  schema v64 — the top-level block is the only fallback).

A REQUIRED key still empty after the merge fails fast at dispatch
(EXEC-RUNTIME-DISPATCH-001): both runtimes require `provider` and `model`
(`thinking` is optional — empty means the runtime decides). Pick values
from `get_runtime_models(workspace_id)`: the view aggregates the ONLINE
workers' declarations, so a typed value corresponds to a worker that can
actually claim the execution. `*` wildcards may appear in the list — a
worker declared `*` accepts whatever the node types.

Edit execution blocks through the workflow draft round-trip:
`get_workflow_draft` / `get_active_workflow` → edit the YAML (top-level
`execution:` and/or per-node overrides) → `validate_workflow` →
`save_workflow_draft` with the CAS flow (`expected_updated_at` from your
last read; a 409 carries `current_draft` for rebasing) →
`compare_workflow` to preview.

### 5.2 Configuring tools (per node and per definition)

`tools` is an allowlist of catalog tool names, resolved per agent node at
dispatch:

- a node-level `tools:` list (agent nodes ONLY — the loader rejects `tools`
  on any non-agent node) overrides the Agent definition's `tools` default
  for that node;
- a node without `tools:` falls back to the definition-level `tools` (the
  `create_agent_definition`/`save_agent_definition_draft` argument).

Unknown tool names fail validation at dispatch. Discover what exists per
runtime with `get_agent_runtimes(workspace_id)`: e.g. for velites, `read`/
`write`/`bash` are `default` tier (preselected), `uuid`/`json` are `opt-in`
(explicitly enabled — add them to `tools` when the node needs them), and
`validate` is `forced` (harness-enforced via `--require-output`; never
select it yourself — listing it is a silent no-op). pi offers the
`read`/`write`/`bash` trio only.

Concrete example — one workflow with a top-level execution default, an agent
node overriding it, a per-node tools selection, and the matching
agent-definition `tools` default:

```yaml
key: education_video_problems_generation
schema_version: 2
execution:                  # workflow-level default for every agent node
  provider: deepseek
  model: v4-flash
nodes:
  intake:
    label: 拉取数据
    capability: fetch_data          # code node: execution/tools ignored
    outputs: [data.json]
  review:
    label: 审核知识点
    capability: review_keywords     # agent node (published Agent exists)
    inputs: [data.json]
    outputs: [review.json]
    execution:              # node override beats the top-level default
      model: v4-pro         # provider still falls through to deepseek
    tools: [read, write, json]      # node tools override the definition's
  report:
    label: 汇总
    capability: report
    inputs: [review.json]
    outputs: [report.md]
edges:
  - {from: intake, to: review}
  - {from: review, to: report}
```

with the Agent definition for `review_keywords` drafted as e.g.
`create_agent_definition(workspace_id, capability="review_keywords",
runtime="velites", skill="keywords/review", tools=["read", "write"])` —
the `review` node's `tools:` list of three tools wins over the
definition's two; an agent node without `tools:` (say `report`, if it is
agent-routed too) uses ITS Agent definition's default.

## 6. Skill editing (create → read → edit → validate → tag)

Skills live in git repos under the skills root (`<skills root>/<group>/<name>`,
in-place is the only mode). A node either follows the repo's live HEAD
(`latest`) or pins a tag frozen in the skill lock. You may create a new skill
under the bound workspace's directory, read any tag, validate the working
tree, and save a new version — you may NEVER relock or publish: a human
reviews the git diff and re-pins.

1. Creating a brand-new skill starts with `create_skill(workspace_id,
   skill_name, files, new_tag, message)` (#633): `skill_name` is one segment
   (`^[a-z0-9][a-z0-9_-]{0,63}$`) and `files` must carry the full contract
   set of FOUR files from the start — non-empty `SKILL.md` +
   `references/output-contract.md` + `scripts/validate_output.py` + a root
   `contract.yaml` (the machine-readable contract, see below; a malformed
   one is rejected like a missing file). The repo is created
   at `<skills root>/<workspace_id>/<skill_name>` with the initial commit
   (author agent-legion-studio) tagged `new_tag` (e.g. `v0.1.0`). Everything
   is validated first; a name that already exists is a 409, and a failed
   create leaves no directory behind, so you can retry safely.
2. `get_skill(workspace_id, skill_key)` — the working tree at HEAD
   (`latest`), or
   `ref=<tag>` to preview one tag, e.g. one another agent just created; an
   unknown tag is a structured 404 and changes
   nothing.
3. Edit the file contents in your draft, then
   `validate_skill(workspace_id, skill_key)` —
   the runtime contract: non-empty SKILL.md + references/output-contract.md +
   scripts/validate_output.py, plus a strict parse of the root
   `contract.yaml` when present. Fix every reported error; reported
   `warnings` (a missing root contract.yaml) do not fail the verdict but
   tell you the machine contract is either on the deprecated embedded
   block or missing entirely.
4. `save_skill_version(workspace_id, skill_key, files, new_tag, message)` —
   writes into the
   skill's in-place repo. Every path is validated before any
   write (inside the skill dir, no `..`/absolute paths, no `.git`, no
   overwriting untracked files); after writing, the contract check re-runs
   and a failure (including a malformed root `contract.yaml`) rolls the
   repo back to its original commit — but a MISSING root `contract.yaml`
   only warns, so existing skills keep saving during the migration window.
   On success it commits (author agent-legion-studio) and tags `new_tag`
   (an existing tag is a conflict); the response carries the `warnings`.
   The skill lock is untouched: tag-pinned nodes keep the
   locked commit, `latest` nodes pick the new HEAD up on their next dispatch.
5. Show the human the git diff of the new tag and ask them to release it:
   re-pin the node's skill ref to the new tag in Studio and relock
   (`make skills-lock`, or let the first dispatch auto-lock). NEVER ask for
   a relock before the human has seen the diff. Publishing/relocking stays
   human-only — you can never do it with these tools.

### Machine-readable output contract (`contract.yaml`)

The machine-readable contract is a standalone root file: `contract.yaml`
in the skill root. Skills created through `create_skill` MUST carry it
from the first commit. Semantics are graded:

- malformed `contract.yaml` (bad YAML, unknown fields, empty `files`,
  absolute/escaping `path`, illegal `format`, `json` without a compilable
  `schema`, `text` with `schema`/`min_chars` misuse) = **error** —
  `validate_skill` reports it and `save_skill_version`/`create_skill`
  reject the save;
- missing `contract.yaml` = **warning** only (the save succeeds and
  reports it): with the contract still on the deprecated embedded block
  you are urged to migrate; with no contract at all the runtime output
  validation degrades to existence-only checks.

```yaml
# contract.yaml — the skill-root machine contract
files:
  - path: script.md              # relative to the job dir, required
    format: text                 # text | json, required
    min_chars: 200               # optional, text only: char count after trimming
    required_headings: ["## 目标"]  # optional, text only: each must appear as a substring
  - path: questions.json
    format: json
    schema:                      # required when format=json: a JSON Schema object
      type: object
      required: [exercises]
```

At run time the harness's built-in contract engine checks it first
(existence, then the per-format checks); cross-file rules and business
semantics stay in prose in `references/output-contract.md` — the engine
does not express them — and `scripts/validate_output.py` remains the
legacy fallback channel for everything the engine cannot say.

Migration promise: the pre-#542 location — a fenced ```yaml contract
block embedded in `references/output-contract.md` — still works but is
DEPRECATED; when a skill has both, the root `contract.yaml` wins
completely. Migrate by moving the block's YAML body into the root file
and keeping the prose. Engine v1 expresses exactly these four check
classes: existence, text length (`min_chars`), required headings, and
JSON Schema. Before asking the human to release a tag, call
`validate_skill` and fix every contract error it reports — a malformed
contract fails validation just like a missing file.

### 6.1 Shared materials across a workspace's skills (#633)

When several skills of one workspace need the SAME reference or script
(a house style guide, a normalization helper), author it ONCE under the
workspace's shared materials instead of copying it into every skill:

```json
// map.json — {"version": 1, "materials": [{"source": "<path>", "skills": [<skill names>]}]}
{
  "version": 1,
  "materials": [
    {"source": "references/prompt-style.md", "skills": ["video-analysis", "video-summary"]},
    {"source": "scripts/normalize.py", "skills": ["video-analysis"]}
  ]
}
```

- `source` is relative to `_shared/` and must stay under `references/` or
  `scripts/`; `skills` lists skill names (the second key segment of
  `<workspace_id>/<skill_name>`).
- Read with `get_shared_materials(workspace_id)`; write the FULL state with
  `save_shared_materials(workspace_id, files)` — `map.json` is just one of
  the files, you author its JSON. Everything is validated before anything
  is written (bad map or paths → 422 listing the problems).
- Sync is AUTOMATIC: every `save_skill_version` of a mapped skill copies
  `_shared/<source>` into that skill's repo at the SAME relative path and
  includes it in the commit (the save response lists them in `synced_files`).
  A synced file that breaks the skill's contract rolls the whole save back,
  like any other file.
- NEVER hand-supply a mapped path in the `save_skill_version` payload —
  the shared copy is authoritative for mapped paths, so the save rejects
  the payload with an error listing the conflicting paths; drop them and
  re-save.
- Relock/publish stays human-only as everywhere else: the sync only lands
  in the LOCAL skill commits, never in the DB skill lock. `_shared` itself
  is not a git repo — the mapped skills' synced commits are the audit trail.

## 7. Common errors and what to do

- `Draft workflow key '...' does not match workspace default workflow key
  '...'` — the workspace already has a key; re-emit the YAML with that key.
- HTTP 409 `Workflow draft conflict` from `save_workflow_draft` — the human
  (or another session) saved a newer draft after your read. Rebase your
  changes onto `current_draft.definition_yaml` from the error and retry with
  `current_draft.updated_at` as the new `expected_updated_at`; never retry
  the stale timestamp.
- `no published node code for ...` — publish the node code first
  (`save_node_code_draft` with `expected_capability`, then publish).
- `Agent capability X must resolve to exactly one published Agent` — draft
  (or ask the human to publish/archive) an Agent definition for X.
- create_agent_definition: 409 — the capability already has an Agent in
  this workspace (the error names it and its status); switch to
  `save_agent_definition_draft` on that Agent. 422 — invalid runtime or
  malformed config_schema/requires_labels. 404 — unknown workspace.
- `node X requires a provider/model ...` at dispatch — a required
  `execution` key resolved empty: set it on the node's `execution:` block
  or the workflow's top-level `execution:` default (EXEC-RUNTIME-DISPATCH-001).
- `node X selects tools not offered by agent runtime ...` — the node (or
  the Agent definition) lists a tool the runtime's catalog does not offer;
  pick names from `get_agent_runtimes` (§5.2).
- `Node X.tools is only valid on an agent node` — move the `tools:` list to
  an agent-routed node; code nodes cannot select tools.
- `node code must define a module-level 'run' function` / `not valid Python`
  — fix the code before re-saving.
- 404 `Unknown workflow node` / `No active workflow revision` on
  save_node_code_draft — you forgot `expected_capability` for a new node.
- save_skill_version: 409 `already has tag` — pick a fresh tag; 422 with an
  `errors` list — fix the reported paths, missing contract files, or the
  malformed `contract.yaml`.
- create_skill: 409 `already exists` — the skill name is taken under this
  workspace; pick another name. 422 with an `errors` list — fix the skill
  name (one lowercase segment), the reported file paths, the missing
  contract set (SKILL.md + references/output-contract.md +
  scripts/validate_output.py + contract.yaml), or the malformed
  `contract.yaml`. 404 — the workspace does not exist.
- `HTTP 401` — token expired/revoked; ask the human to mint a new one.

Golden rule: validate first, compare second, present third — then request
the publish and let the human decide.
