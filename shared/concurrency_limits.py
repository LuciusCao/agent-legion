"""The single declared ceiling for per-Worker concurrency (issue #657).

``MAX_DYNAMIC_CONCURRENCY`` bounds what one Worker may declare for its
agent and code pools — validated locally at config load/hot-reload
(``worker/runtime/controls.py``) AND at the Host's registration/claim
contracts (``agent_workers_contracts`` / ``agent_worker_claim_contracts``).
Before #657 the two sides each hardcoded 1024; both now reference THIS
constant and the contract test pins their equality, so the pair cannot
drift again.

2048 is a contract-domain ceiling, not a single-machine target: 1024 slots
measured ~14.2 GB RSS on the reference host, so 2048 would need ~28 GB of
fleet memory — the value domain admits it, hosts size it. DB-level storage
(``agent_workers.max_concurrency``) keeps its plain > 0 CHECK.
"""

from __future__ import annotations

MAX_DYNAMIC_CONCURRENCY = 2048
