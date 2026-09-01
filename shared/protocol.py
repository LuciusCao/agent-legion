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

Field-level deprecations ride without a version bump while the wire shape is
unchanged: the claim body's workflow_key is deprecated (#211 Phase 2 — equals
workspace_id since schema v62); its removal is gated on the Phase 3/4 window
and will carry a version bump.
"""

CODE_PROTOCOL_VERSION = 2
MODEL_RUNTIME_PROTOCOL_VERSION = 3
ARTIFACT_GZIP_PROTOCOL_VERSION = 4

# The protocol version this software declares at registration. Equals the
# latest feature version above.
PROTOCOL_VERSION = ARTIFACT_GZIP_PROTOCOL_VERSION
