"""First node of question_comprehension_info: parse user input, fetch from CMS.

Intake fans out opaque candidates (``entity_id`` is the raw question id or
knowledge code); this node is the single place that talks to the CMS. The
intake mode is read from the prefetched batch payload: ``knowledge_codes``
input expands one job per code into a multi-question ``questions.json``;
anything else treats ``source_id`` as a single question id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.cms import urls as cms_urls
from workspace_libs.cms.client import CmsClientError, check_in_band_error, get_token
from workspace_libs.cms.question import fetch_question_detail, list_questions_by_knowledge
from workspace_libs.node_sdk import NodeContext, parse_json_object


class CmsEmptyStemError(RuntimeError):
    """Fetched question has no usable stem.

    Garbage source data, not a credential problem: classified
    business/source_missing (see _failure_classification_rules), so the node
    neither invalidates the connection token nor looks retryable.
    """


def _report_if_auth_failure(ctx: NodeContext, exc: CmsClientError) -> None:
    """Invalidate the cached connection token only for auth-semantics failures.

    HTTP 401/403 and known in-band auth codes carry ``auth_failure=True``;
    transport failures (5xx/timeout/DNS) and non-auth in-band errors leave
    the healthy token alone (the transient retry path re-uses it). The node
    only records the fact; the parent executor performs the invalidation
    (design: node-sdk-and-worker-execution §5.3).
    """
    if exc.auth_failure:
        ctx.report_auth_failure()


# Retired node-level connection keys (pre-connection era). They are honored
# only when no connection was injected (legacy frozen payloads); a resolved
# connection always wins.
_LEGACY_CONNECTION_KEYS = ("token", "env", "base_url", "api_url", "question_list_url")


def _intake_input_field(ctx: NodeContext) -> str:
    # The dispatch layer prefetches the batch row (nodes hold no database
    # handle, EXEC-CODE-003); runtimes without a prefetch yield "".
    batch = ctx.batch
    if not batch:
        return ""
    payload = parse_json_object(batch.get("source_payload_json"))
    mode = payload.get("intake_mode")
    if isinstance(mode, dict):
        return str(mode.get("input_field") or "")
    return ""


def _detail_payload(detail: Any, fallback_id: str, fallback_title: str) -> dict[str, Any]:
    return {
        "question_id": detail.question_id or fallback_id,
        "title": detail.title or fallback_title,
        "normalized": detail.normalized,
        "cms_payload": detail.payload,
    }


def _validate_fetched_detail(detail: Any, question_id: str) -> None:
    """Fail fast on CMS error payloads and empty stems.

    The CMS detail endpoint signals auth/parameter failures in-band via a
    non-zero ``code`` with ``data: null``; treating such payloads as valid
    questions poisons every downstream node (empty stem → empty key info →
    business validation failures). ``check_in_band_error`` re-checks the
    payload here because tests and forks may bypass the workspace_libs
    fetch; its ``CmsClientError`` classifies as technical/cms_auth (see
    _failure_classification_rules), so the job stops at fetch instead of
    forwarding garbage. An empty stem means the source question itself is
    unusable — a business/source_missing failure via ``CmsEmptyStemError``,
    not an auth problem. In knowledge mode a single bad detail fails the
    whole batch before anything is written — intentional: auth failures are
    systemic, and a partial questions.json would silently drop the
    remaining questions of the batch.
    """
    check_in_band_error(getattr(detail, "payload", None), f"question_id={question_id}")
    normalized = getattr(detail, "normalized", None)
    stem = normalized.get("stem") if isinstance(normalized, dict) else None
    if not str(stem or "").strip():
        raise CmsEmptyStemError(f"CMS 响应缺少题干 (question_id={question_id})")


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    log = ctx.logger
    ctx.checkpoint()
    source_id = str(job["source_id"])
    knowledge_mode = _intake_input_field(ctx) == "knowledge_codes"
    log.info(
        "question_intake: source_id=%s title=%s mode=%s",
        source_id,
        job.get("title", ""),
        "by_knowledge" if knowledge_mode else "by_id",
    )

    cms_config = ctx.service_config(legacy_keys=_LEGACY_CONNECTION_KEYS)
    node_config = ctx.config
    if node_config:
        log.info("  node_config overrides: %s", sorted(node_config))

    questions: list[dict[str, Any]] = []
    if knowledge_mode:
        list_url = str(
            cms_config.get("question_list_url") or cms_urls.question_list_url(cms_config)
        )
        if not list_url:
            raise RuntimeError(
                f"knowledge mode requires a CMS question list URL (code={source_id})"
            )
        detail_url = str(
            cms_config.get("api_url")
            or cms_config.get("question_detail_url")
            or cms_urls.question_detail_url(cms_config)
        )
        token = get_token(str(cms_config.get("env", "")), cms_config)
        try:
            summaries = list_questions_by_knowledge(source_id, list_url, token)
        except CmsClientError as exc:
            _report_if_auth_failure(ctx, exc)
            raise
        if not summaries:
            raise RuntimeError(f"no questions found for knowledge code: {source_id}")
        log.info("  CMS returned %d question(s) for code=%s", len(summaries), source_id)
        for summary in summaries:
            ctx.checkpoint()
            try:
                detail = fetch_question_detail(summary.question_id, detail_url, token)
                _validate_fetched_detail(detail, summary.question_id)
            except CmsClientError as exc:
                _report_if_auth_failure(ctx, exc)
                raise
            questions.append(_detail_payload(detail, summary.question_id, summary.title))
    else:
        api_url = str(
            cms_config.get("api_url")
            or cms_config.get("question_detail_url")
            or cms_urls.question_detail_url(cms_config)
        )
        if api_url:
            log.info("  fetching from CMS: %s", api_url)
            token = get_token(str(cms_config.get("env", "")), cms_config)
            try:
                detail = fetch_question_detail(source_id, str(api_url), token)
                _validate_fetched_detail(detail, source_id)
            except CmsClientError as exc:
                _report_if_auth_failure(ctx, exc)
                raise
            ctx.checkpoint()
            questions.append(_detail_payload(detail, source_id, str(job["title"])))
        else:
            log.info("  no CMS configured, using base payload")
            questions.append(
                {
                    "question_id": source_id,
                    "title": job["title"],
                    "normalized": {},
                    "cms_payload": None,
                }
            )

    out_path = ctx.artifacts.write_json("questions.json", {"questions": questions})
    log.info("  wrote %s (%d question(s))", out_path.name, len(questions))
