from pydantic import BaseModel, Field

from server.app.routes.agent_catalog_contracts import AgentDefinitionResponse


class ExecutorCatalogResponse(BaseModel):
    """Execution catalog for Studio (P-0.5 step 2: Agents only).

    The ``executors`` half retired with the executor concept (schema v47);
    the response type keeps the pre-retirement name until the step-3
    contract cleanup.
    """

    agents: list[AgentDefinitionResponse] = Field(default_factory=list)
