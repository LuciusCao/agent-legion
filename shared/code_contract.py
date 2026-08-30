"""Cross-process contract constants for code-node execution (#282).

Separated from ``shared/code_sandbox`` (argv/env plumbing) so that the
contract surface — member names, the result-metadata key set — reads as its
own artifact. Still stdlib-only: the worker image ships shared/ wholesale
without a repo checkout.

Lives here rather than only in code_sandbox because these constants are the
Worker↔Host boundary itself: the Worker writes exactly these members/keys
(``worker/code_runner.py``), the Host reads them
(``server/app/agent_broker/result_unpack.py`` for members,
``server/app/routes/agent_worker_results.py`` for metadata keys), and the
guard tests in ``tests/workers/test_protocol_sync.py`` pin both sides to
this single definition.
"""

from __future__ import annotations

# Code bundle member names (batch 2 contract, shared by the Host-side packer
# server/app/agent_broker/agent_bundle.py and the Worker-side runner).
CODE_BUNDLE_NODE_FILE = "node_code.py"
CODE_BUNDLE_LIBS_DIR = "workspace_libs"
# Result-archive member carrying the node's captured stdout/stderr for
# kind='code' results (batch 2 decision 10); the Host promotes it to the
# run's canonical log path.
CODE_RESULT_LOG_MEMBER = "node.log"
# Mirrors workspace_libs/node_sdk.py NODE_RUNTIME_DIR / AUTH_FAILURE_MARKER.
# node_sdk must stay import-self-contained (the code bundle ships only the
# workspace_libs snapshot), so that mirror keeps a comment pointer instead of
# importing this module. Equality with the node_sdk side is pinned by
# tests/workers/test_protocol_sync.py (issue #282).
AUTH_FAILURE_MARKER_PATH = ".node_runtime/auth_failure"
# Connection keys reported by node code via report_auth_failure; bounded on
# both sides (Host route agent_worker_results, Worker result metadata).
MAX_CONNECTION_KEY_CHARS = 128
# Keys of the kind='code' result-metadata dict (issue #282): the Worker's
# ``prepare_code_result`` (worker/code_runner.py) writes exactly these —
# ``auth_failure_connection`` only when the node actually reported one — and
# the Host reads them in ``parse_result_metadata``
# (server/app/routes/agent_worker_results.py) via ``.get`` with defaults, so
# an absent optional key is not an error. A process-boundary contract with no
# compiler and no schema; before #282 both sides were handwritten literals
# kept in sync by comment only. ``run_dir`` is deliberately NOT part of this
# set: it is agent-path-only and code results never carry it.
CODE_RESULT_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "exit_code",
        "error_message",
        "command",
        "output_artifacts",
        "auth_failure_connection",
    }
)
