from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries as JobQueries

__all__ = ["JobQueries"]


def __getattr__(name: str) -> Any:
    if name != "JobQueries":
        raise AttributeError(name)
    from server.app.jobs.queries import JobQueries

    return JobQueries
