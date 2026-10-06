"""Exception types for the Worker Host client.

Split out of ``client.py`` for the file budget; ``worker.host.client``
re-exports these names so existing import sites keep working.
"""

from __future__ import annotations

import requests


class WorkerAuthError(RuntimeError):
    """Server rejected this Worker as unknown or revoked; re-registration is required."""


class TransientHostError(requests.RequestException):
    """Host answered with a transient failure (5xx/429); retrying is correct.

    A RequestException subclass on purpose: the retry loop treats transport
    failures and these answers alike as "Host temporarily unavailable", while
    WorkerAuthError (a verdict) and programming errors still fail fast.
    """


class HostResponseError(RuntimeError):
    """Host answered the claim poll off-contract (#960): an unexpected HTTP
    status or an undecodable / misshapen body.

    A dedicated type so the executor's claim loop can back off on exactly
    "the Host side misbehaved" without a blanket ``except Exception`` that
    would also disguise Worker-side programming errors (TypeError/KeyError
    in the claim pass) as an outage. Subclasses RuntimeError so callers
    matching the pre-#960 ``RuntimeError`` contract keep working.
    """


# #960：claim 轮询的「Host 暂时不可用」族——传输错误（含 TransientHostError）
# 与 Host 不合契约应答。executor claim 循环只对这一族退避，其余异常上抛。
HOST_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    requests.RequestException,
    HostResponseError,
)
