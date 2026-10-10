"""Exit-code verdicts and the shared error_message bound for the upload
bulk lane.

Split out of ``prepare.py`` for the file budget. The failed-result payload
writers and the degraded archive writers (the v2 recycle targets: every
reported archive must carry the metadata being reported as its
``result.json`` member) moved one step further into
``worker/upload/degraded_archive.py`` (#1168/#1169 收口轮的按主题拆分)；
this module keeps the exit-code verdict face and the error_message
truncation bound both sides share.
"""

from __future__ import annotations

from worker.upload.stderr_evidence import stderr_error_message

MAX_ERROR_MESSAGE_CHARS = 4000


def exit_verdict(exit_code: int, failure: str | None, stderr_tail: bytes) -> tuple[str, str]:
    """(status, error_message) of a finished agent process. ``failure`` is the
    event-scan attribution the caller already gated (exit-0 model error or the
    #952 output-truncation attribution); it outranks the exit-code faces."""
    if exit_code == 130:
        return "cancelled", "Agent Worker is shutting down"
    if failure:
        return "failed", failure
    if exit_code == 0:
        return "completed", ""
    if exit_code == 124:
        # Timeout kill (synthetic 124 from wait_for_exit): the attribution
        # face (error_message) keeps the established timeout wording (#609)
        # untouched — but the EVIDENCE face (agent_stderr_tail) still
        # rides along (#755 终审 P3-1): attribution and evidence are
        # decoupled, the partial-run stderr stays available for diagnosis.
        return "failed", "Agent process timed out"
    return "failed", stderr_error_message(exit_code, stderr_tail)
