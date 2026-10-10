"""Agent definition catalog (issue #191).

``AgentDefinition`` — the versioned, DB-published agent declaration model —
plus the builtin demo-workflow templates. Consumers across services, routes
and the broker import the model from the package facade; the agent_id
charset predicate and its error detail (#1167/#1173) live in
``agent_catalog.definition`` and are imported directly from the submodule.
"""

from server.app.agent_catalog.definition import AgentDefinition as AgentDefinition

__all__ = ["AgentDefinition"]
