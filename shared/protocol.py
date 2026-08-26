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
"""

CODE_PROTOCOL_VERSION = 2
MODEL_RUNTIME_PROTOCOL_VERSION = 3

# The protocol version this software declares at registration. Equals the
# latest feature version above.
PROTOCOL_VERSION = MODEL_RUNTIME_PROTOCOL_VERSION
