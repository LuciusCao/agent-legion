"""The CONFIG-RUNTIME-TIMEOUT-001 matrix dimensions (#691/#869).

Shared by ``tests/services/test_runtime_timeout_matrix.py`` (drives every
case through the real paths) and ``test_runtime_timeout_paths_guard.py``
(requires every execution construction site to map onto ``PATHS``); test
modules must not import each other, so the table lives here.
"""

from __future__ import annotations

from typing import Any

# The matrix path table: local code pool, remote single/batch claim, legacy
# queued request, and the two shard execution paths (#869).
PATHS = ("local", "single", "batch", "legacy", "local_shard", "remote_shard")
LOCAL_PATHS = ("local", "local_shard")
CODE_ONLY_PATHS = (*LOCAL_PATHS, "remote_shard")

# layer state -> (L1 node config timeout, L2 workspace override timeout)
LAYERS: dict[str, tuple[Any, Any]] = {
    "L0": (None, None),
    "L1": (900, None),
    "L2": (None, 2400),
    "L1+L2": (900, 2400),
    "invalid_L2": (900, "soon"),
}
TIMINGS = ("before_intake", "after_intake", "after_enqueue", "after_decision")


def matrix_cases() -> list[tuple[str, str, str, str]]:
    cases = []
    for kind in ("agent", "code"):
        for path in PATHS:
            if kind == "agent" and path in CODE_ONLY_PATHS:
                continue  # agent nodes never run in the local pool / shard
            for layer in LAYERS:
                if path == "legacy" and layer in ("L1", "L1+L2"):
                    continue  # legacy base already folds L1 in
                for timing in TIMINGS:
                    if path in LOCAL_PATHS and timing == "after_enqueue":
                        continue  # no enqueue on the local path
                    if path == "legacy" and timing in ("before_intake", "after_intake"):
                        continue  # legacy requests predate the change
                    cases.append((kind, path, layer, timing))
    return cases
