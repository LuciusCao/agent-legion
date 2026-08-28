"""Composition-root assembly groups (issue #190).

Each module builds one domain group of the application graph so
``server/app/main.py`` stays a readable orchestration of groups instead of
25 inline constructors.
"""

from server.app.bootstrap.agent_plane import AgentPlane as AgentPlane
from server.app.bootstrap.agent_plane import build_agent_plane as build_agent_plane

__all__ = ["AgentPlane", "build_agent_plane"]
