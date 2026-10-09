"""Agent Worker registration/claim protocol versions.

Single source of truth shared by the Host (server/app) and the Worker
(worker/) — the worker image ships only worker/ + shared/, so before this
module the constants lived on both sides with "bump both together" comments.
The registration response field is ``RegisterAgentWorkerResponse
.host_protocol_version`` (server/app/routes/agent_workers_contracts.py).

Version history:
- 1: baseline registration + agent-claim pull protocol.
- 2 (CODE_PROTOCOL_VERSION): kind='code' claims, heartbeat cancel bodies and
  cancel acknowledgements. Workers below this never receive code claims.
- 3 (MODEL_RUNTIME_PROTOCOL_VERSION): runtime-scoped model declarations; a v3
  worker must fail closed against an older Host that erases model runtimes.
- 4 (ARTIFACT_GZIP_PROTOCOL_VERSION, #338): gzip-compressed artifact objects.
  v4+ Workers receive ``.gz``-suffixed upload specs (PUT compressed bytes,
  report the uncompressed sha256) and ``content_encoding: "gzip"`` input refs
  (gunzip while downloading); older Workers keep bare keys and never see a
  ``.gz`` input upgrade, so a mixed fleet never mismatches on the stored form.
- 5 (HEARTBEAT_BATCH_PROTOCOL_VERSION, #352): per-Worker batch heartbeat.
  v5 Workers run one heartbeat loop per machine: a single
  ``POST /api/agent-executions/heartbeats`` renews every claimed lease of
  that Worker in one write transaction. The single execution endpoint is
  unchanged (a mixed fleet is served by the same Host); a v5 Worker that
  meets a pre-v5 Host gets 404 and falls back to per-execution beats.

Field-level deprecations ride without a version bump while the wire shape is
unchanged; a deprecated field's removal rides without one too once no
supported Worker reads it. The claim body's workflow_key (#211, equal to
workspace_id since schema v62) is removed in M3 without a bump: no Worker
since the #303 release (v0.5.0) reads it — Workers key on workspace_id — and
every Worker older than that is already outside the closed #547 window. The
manifest's workflow_key (exposed to node code via the runtime dict) is a
separate surface and stays.

Response-shape retirement without a bump (#547): the claim endpoint answers
``BatchAgentClaimResponse`` (``{"claims": [...]}``, one element at the
default limit=1) for EVERY request — the pre-#546 single-object body is gone.
No version bump because the only affected fleet is pre-0.7.4 Workers (they
never send ``limit``); that window is closed. A v5 Worker's
``claim_batch`` shape-sniff still wraps a single object from an older Host
during a Host downgrade, so the mixed-fleet direction that matters (new
Worker, old Host) keeps working.

Additive field without a bump (#590): the batch heartbeat response carries a
``settled`` list (completion followups the Host classified inside the beat
transaction). Old Workers ignore the unknown key; new Workers parse a
settled-less body from an older Host as an empty list — both directions ride
v5 safely.

Result-metadata dual shape without a bump (#843): a report carrying
``X-Agent-Result-Format: 2`` moves the metadata JSON from the
``X-Agent-Result`` header into the result-archive member ``result.json``
(header budget and the #748/#755 degrade chain do not apply; constants in
shared/code_contract.py). The Host has read both shapes since v0.7.19; the
Worker-side write switch rides a later PR, so every shipping Worker stays v1
and mixed fleets are unaffected. A v2-writing Worker against a pre-v0.7.19
Host is refused with 400 (no v1 header JSON to parse).
"""

CODE_PROTOCOL_VERSION = 2
MODEL_RUNTIME_PROTOCOL_VERSION = 3
ARTIFACT_GZIP_PROTOCOL_VERSION = 4
HEARTBEAT_BATCH_PROTOCOL_VERSION = 5

# The protocol version this software declares at registration. Equals the
# latest feature version above.
PROTOCOL_VERSION = HEARTBEAT_BATCH_PROTOCOL_VERSION
