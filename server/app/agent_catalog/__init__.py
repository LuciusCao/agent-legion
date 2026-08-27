"""Agent definition catalog (issue #191).

``AgentDefinition`` — the versioned, DB-published agent declaration model —
plus the builtin demo-workflow templates. Consumers across services, routes
and the broker import the model from the package facade.
"""

from server.app.agent_catalog.definition import AgentDefinition as AgentDefinition

__all__ = ["AgentDefinition"]
