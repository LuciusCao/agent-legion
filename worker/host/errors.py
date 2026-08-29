"""Exception types for the Worker Host client.

Split out of ``client.py`` for the file budget; ``worker.host.client``
re-exports both names so existing import sites keep working.
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
