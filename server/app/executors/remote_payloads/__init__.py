"""Remote payload builders: manifest/bundle construction per payload kind.

The registry maps a payload name (``RemoteExecutorConfig.payload``) to a
factory that builds a ``PayloadBuilder`` from runtime dependencies and the
executor's capability configs. Importing this package registers the builtin
builders (see the imports at the bottom).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.executors.config import RemoteCapabilityConfig
    from server.app.executors.kinds import RuntimeDependencies
    from server.app.executors.models import ExecutionContext


class PayloadBuilder(Protocol):
    """Builds the payload a RemoteExecutor ships to remote workers."""

    name: str

    def build_manifest(self, context: ExecutionContext) -> dict[str, Any]: ...

    def build_bundle_for(self, context: ExecutionContext, bundle_path: Path) -> None: ...

    def build_command_spec(self, manifest: dict[str, Any]) -> dict[str, Any]: ...

    def scan_error(self, events_file: Path) -> str | None: ...

    def cleanup(self, context: ExecutionContext) -> None:
        """Release per-execution payload state (skill copies, staged manifests)."""
        ...


PayloadBuilderFactory = Callable[
    ["RuntimeDependencies", dict[str, "RemoteCapabilityConfig"]], "PayloadBuilder"
]

_PAYLOAD_BUILDERS: dict[str, PayloadBuilderFactory] = {}


def register_payload_builder(name: str, factory: PayloadBuilderFactory) -> None:
    if name in _PAYLOAD_BUILDERS:
        raise ValueError(f"payload builder {name!r} is already registered")
    _PAYLOAD_BUILDERS[name] = factory


def get_payload_builder(name: str) -> PayloadBuilderFactory:
    try:
        return _PAYLOAD_BUILDERS[name]
    except KeyError:
        raise KeyError(f"unknown payload builder {name!r}") from None


def has_payload_builder(name: str) -> bool:
    return name in _PAYLOAD_BUILDERS


# Register the builtin payload builders; keep at the bottom so the registry
# above is fully defined before the submodule factory is referenced (the
# submodule must not import this package back, otherwise the architecture
# import-cycle check reports __init__ <-> pi).
from server.app.executors.remote_payloads.pi import build_pi_payload  # noqa: E402

register_payload_builder("pi", build_pi_payload)
