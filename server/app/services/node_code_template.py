"""Minimal SDK skeleton template for a new custom workflow node.

Single source for the Studio「从模板新建」entry: the template evolves with the
Node SDK (``workspace_libs.node_sdk.NodeContext``) and is served read-only by
the node code route. It must stay directly runnable: a model or human who has
read the SDK fills in the business logic without touching the scaffolding.
"""

# The template ships as source text, not an importable module: custom node
# code is user data stored in the DB (EXEC-CODE-002) and runs inside the
# velites OS sandbox (EXEC-CODE-003).
NODE_CODE_TEMPLATE = '''\
"""Custom workflow node — fill in the business logic in run().

Entry contract: module-level run(job, job_dir, runtime), same as builtin
nodes (EXEC-CODE-002). Use the Node SDK instead of hand-rolling JSON IO or
config merging. Custom code runs inside the velites OS sandbox
(EXEC-CODE-003): the filesystem is read-only except job_dir, and network is
denied unless the capability opts in via sandbox_network.
"""

from workspace_libs.node_sdk import NodeContext


def run(job, job_dir, runtime):
    ctx = NodeContext(job, job_dir, runtime)
    ctx.logger.info("custom node start")

    # Dispatch-resolved node config: capability config_schema defaults with
    # node/workspace overrides applied and secret refs already resolved.
    config = ctx.config

    # Read an input artifact produced by an upstream node (rename to match
    # the node's declared inputs).
    payload = ctx.artifacts.read_json_object("input.json")

    # TODO: business logic here.
    result = {"input": payload, "config": dict(config)}

    # Write every declared output artifact; write_json checkpoints
    # cancellation before committing the file (cooperative cancellation).
    ctx.artifacts.write_json("output.json", result)
'''
