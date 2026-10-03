"""Runtime-mutable node config keys (CONFIG-RUNTIME-MUTABLE-001).

Split from ``node_config`` for the file-size budget. A config_schema property
declared ``runtime_mutable: true`` opts out of the intake freeze: dispatch
re-resolves only those keys along the usual chain (defaults → node config →
workspace override) so run switches (``dry_run``, ``upload_enabled``, …) take
effect on the next node execution of an already-intaken job. Everything else
stays frozen for job reproducibility (EXEC-CODE-002's config analogue).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.workflows.node_config_schema import RESERVED_EXECUTION_KEYS


def runtime_mutable_keys(config_schema: Mapping[str, Any]) -> frozenset[str]:
    """Property names declared ``runtime_mutable: true`` in *config_schema*.

    The platform-reserved execution keys (``timeout_seconds`` /
    ``sandbox_network``) are never selected by the marker: node config_schemas
    cannot redeclare them (the workflow loader rejects that) and the marker is
    ignored here even if a hand-built schema carries it. Their mutability is
    platform-classified instead (#691, ``runtime_reserved_config``):
    ``timeout_seconds`` is re-resolved at dispatch by ``dispatch_config_resolution``
    directly, ``sandbox_network`` stays intake-frozen. Keeping the reserved
    keys out of this set also keeps them out of the inherit-upgrade exclusion
    (a timeout does not change what a node produces).
    """
    properties = config_schema.get("properties") if isinstance(config_schema, Mapping) else None
    if not isinstance(properties, Mapping):
        return frozenset()
    return (
        frozenset(
            name
            for name, prop in properties.items()
            if isinstance(prop, Mapping) and prop.get("runtime_mutable") is True
        )
        - RESERVED_EXECUTION_KEYS
    )
