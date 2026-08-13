# Compatibility shim: the implementation lives in workspace_libs (节点自足化,
# batch 2 C1). Kept only because frozen custom nodes inside the code sandbox
# may still import the old server.app.workflows path (the sandbox read
# allowlist includes server/). New code must import from workspace_libs.
from workspace_libs.question_fingerprint import (
    compute_question_fingerprint,
    extract_cms_fingerprint,
)

__all__ = ["compute_question_fingerprint", "extract_cms_fingerprint"]
