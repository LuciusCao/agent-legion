from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings


def resolve_node_executors(
    workspace_id: str,
    workflow_key: str,
    workspace_executor_config: WorkspaceExecutorConfigurationService,
    settings: Settings,
) -> dict[str, tuple[str | None, str | None]]:
    """Resolve executor bindings for a workspace to a node-key mapping.

    Returns a mapping from node key to a tuple of (executor_id, executor_kind).
    If workspace executor configuration is unavailable, returns an empty dict.
    """
    try:
        config = workspace_executor_config.get(workspace_id)
    except Exception:
        return {}

    executor_kinds = {
        executor_id: executor.kind
        for executor_id, executor in settings.executor_definitions.items()
    }

    return {
        binding["node_key"]: (
            binding.get("executor_id"),
            executor_kinds.get(binding.get("executor_id")),
        )
        for binding in config.get("bindings", [])
        if binding.get("workflow_key") == workflow_key and binding.get("node_key")
    }
