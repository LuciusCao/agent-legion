from typing import cast

from fastapi import Request

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.jobs import JobQueries


def get_db(request: Request) -> Database:
    return cast(Database, request.app.state.db)


def get_job_queries(request: Request) -> JobQueries:
    return cast(JobQueries, request.app.state.job_db)


def get_agent_manager(request: Request) -> AgentStatusManager:
    return cast(AgentStatusManager, request.app.state.agent_manager)
