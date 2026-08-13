# Compatibility shim: the implementation lives in workspace_libs (节点自足化,
# batch 2 C1). Kept only because frozen custom nodes inside the code sandbox
# may still import the old server.app.workflows path (the sandbox read
# allowlist includes server/). New code must import from workspace_libs.
from workspace_libs.comprehension_contract import assert_comprehension_lists_contract

__all__ = ["assert_comprehension_lists_contract"]
