"""Recover undelivered receipts without replaying historical task wakeups.

Durable status messages are scoped by both chat and ACP session. Legacy
messages without ACP identity cannot safely identify tasks after a fresh
ACP session replaces an old one, so they are not recovery evidence.
"""

from __future__ import annotations

from server.app.jobs import JobQueries


def recovery_sets(
    db: JobQueries, session_id: str, acp_session_id: str, initial_terminal: set[str]
) -> tuple[set[str], set[str]]:
    observed: set[str] = set()
    finished: set[str] = set()
    before_seq = 2**63 - 1
    while True:
        rows = db.list_studio_chat_messages_tail(session_id, before_seq=before_seq)
        for row in rows:
            content = row.get("content")
            if row.get("kind") != "status" or not isinstance(content, dict):
                continue
            if content.get("acp_session_id") != acp_session_id:
                continue
            task_id = content.get("task_id")
            if not isinstance(task_id, str):
                continue
            if content.get("event") == "background_task_status":
                observed.add(task_id)
            elif content.get("event") == "background_task_finished":
                finished.add(task_id)
        if len(rows) < 500:
            break
        before_seq = rows[0]["seq"]
    # Unobserved historical terminals remain ignored. Recovery receipts must
    # not wake the model: cancellation intent is not persisted across resume.
    recovered = initial_terminal & observed - finished
    return (initial_terminal - recovered) | finished, recovered
