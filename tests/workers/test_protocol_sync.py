"""Cross-side protocol constant synchronization.

The registration protocol versions live once in shared/protocol.py (the
worker image ships shared/); both the Worker declaration and the Host
contract default must derive from that single copy. Before this module the
two sides carried independent literals with "bump both together" comments
and no test noticed drift.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from shared.protocol import (
    CODE_PROTOCOL_VERSION,
    MODEL_RUNTIME_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
)
from worker.host_client import Client

pytestmark = pytest.mark.no_db


def test_worker_declared_version_is_latest_shared() -> None:
    assert Client.__module__  # import sanity
    from worker.host_client import PROTOCOL_VERSION as worker_declared

    assert worker_declared is PROTOCOL_VERSION
    assert PROTOCOL_VERSION == MODEL_RUNTIME_PROTOCOL_VERSION


def test_host_contract_default_matches_shared() -> None:
    from server.app.routes.agent_workers_contracts import RegisterAgentWorkerResponse

    field = RegisterAgentWorkerResponse.model_fields["host_protocol_version"]
    assert field.default == MODEL_RUNTIME_PROTOCOL_VERSION


def test_server_registry_constants_match_shared() -> None:
    from server.app.agent_workers import (
        CODE_PROTOCOL_VERSION as server_code,
    )
    from server.app.agent_workers import (
        MODEL_RUNTIME_PROTOCOL_VERSION as server_model_runtime,
    )

    assert server_code is CODE_PROTOCOL_VERSION
    assert server_model_runtime is MODEL_RUNTIME_PROTOCOL_VERSION


def test_pydantic_default_binding_is_stable() -> None:
    # The contract default binds the shared constant at class-creation time;
    # a plain literal regression (host_protocol_version: int = 3) would keep
    # passing the value check above only until the shared constant bumps —
    # assert identity of the annotation source instead.
    from server.app.routes.agent_workers_contracts import (
        RegisterAgentWorkerResponse as Response,
    )

    assert issubclass(Response, BaseModel)
    assert Response.model_fields["host_protocol_version"].default is MODEL_RUNTIME_PROTOCOL_VERSION
