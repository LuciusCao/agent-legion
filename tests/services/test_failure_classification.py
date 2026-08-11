from server.app.services.failure_classification import (
    FAILURE_CATEGORIES,
    classify_failure,
    is_transient_retryable,
    resolve_failure_fields,
)


def test_category_vocabulary_is_fixed():
    assert set(FAILURE_CATEGORIES) == {"technical", "business", "unknown"}


def test_review_rejected_is_business():
    assert classify_failure(1, "review_rejected: key info item 2 is wrong") == (
        "business",
        "review_rejected",
    )


def test_exit_code_124_is_timeout():
    assert classify_failure(124, "") == ("technical", "timeout")


def test_agent_process_exited_124_message_is_timeout():
    assert classify_failure(None, "Agent process exited 124") == ("technical", "timeout")


def test_raw_terminated_is_provider_stream():
    assert classify_failure(1, "terminated") == ("technical", "provider_stream")


def test_terminated_matches_as_independent_word_only():
    assert classify_failure(1, "the run was terminated by signal") == (
        "technical",
        "provider_stream",
    )
    assert classify_failure(1, "preterminated state") == ("unknown", "unknown")


def test_connection_error_is_provider_stream():
    assert classify_failure(1, "Connection error.") == ("technical", "provider_stream")


def test_pi_wrapped_stream_errors_are_provider_stream():
    assert classify_failure(1, "Pi model call failed: terminated") == (
        "technical",
        "provider_stream",
    )
    assert classify_failure(1, "Pi model call failed: Connection error.") == (
        "technical",
        "provider_stream",
    )


def test_pi_model_call_failure_without_stream_pattern_is_provider_request():
    assert classify_failure(1, "Pi model call failed: HTTP 500 from provider") == (
        "technical",
        "provider_request",
    )


def test_cms_auth_errors():
    assert classify_failure(1, "CmsClientError: token refresh failed") == (
        "technical",
        "cms_auth",
    )
    assert classify_failure(1, "CMS token expired") == ("technical", "cms_auth")
    # question_intake fail-fast on in-band CMS error payloads (schema v34).
    assert classify_failure(
        1, "CmsClientError: CMS 返回错误: code=10015 message=JWT验证失败 (question_id=q-1)"
    ) == ("technical", "cms_auth")


def test_cms_empty_stem_is_business_source_missing():
    # 空题干是源数据本身不可用（CmsEmptyStemError）：重跑无用，也不是凭据问题。
    assert classify_failure(1, "CmsEmptyStemError: CMS 响应缺少题干 (question_id=q-1)") == (
        "business",
        "source_missing",
    )


def test_cms_transport_failures_are_transient_cms_request():
    # 5xx/超时/DNS 等传输层失败不是凭据问题，走 lease finish 的有限次重试。
    category, detail = classify_failure(
        1, "CmsClientError: CMS request failed: 500 Server Error: Internal Server Error"
    )
    assert (category, detail) == ("technical", "cms_request")
    assert is_transient_retryable(detail)


def test_cms_transport_timeout_is_cms_request_not_timeout():
    # CMS 读超时消息同时含 "timed out"：必须先命中 cms_request。
    category, detail = classify_failure(
        1,
        "CmsClientError: CMS request failed: HTTPSConnectionPool(host='cms.example', "
        "port=443): Read timed out. (read timeout=15)",
    )
    assert (category, detail) == ("technical", "cms_request")
    assert is_transient_retryable(detail)


def test_dispatch_connection_failures_are_technical_connection_config():
    assert classify_failure(None, "connection 'cms-internal' 不存在") == (
        "technical",
        "connection_config",
    )
    assert classify_failure(None, "connection 'cms-internal' 已停用") == (
        "technical",
        "connection_config",
    )
    assert classify_failure(
        None, "connection 'cms-internal' 获取 token 失败: CMS token request failed: down"
    ) == ("technical", "connection_config")
    # 包裹的原因里含 "timed out" 时也必须归 connection_config 而非 timeout。
    assert classify_failure(
        None,
        "connection 'cms-internal' 获取 token 失败: CMS token request failed: "
        "HTTPSConnectionPool(host='t.example', port=443): Read timed out.",
    ) == ("technical", "connection_config")


def test_resource_limit_errors():
    assert classify_failure(1, "OSError: [Errno 24] Too many open files") == (
        "technical",
        "resource_limit",
    )
    assert classify_failure(1, "Too many open files") == ("technical", "resource_limit")


def test_missing_outputs_is_output_missing():
    # A finished run without artifacts is technical: with a mature workflow the
    # pipeline, not the content, is the usual suspect.
    assert classify_failure(1, "Missing outputs: key_info.json") == (
        "technical",
        "output_missing",
    )
    assert classify_failure(1, "Agent Worker did not report output artifacts") == (
        "technical",
        "output_missing",
    )


def test_unpack_failure_is_technical_execution_error():
    assert classify_failure(1, "failed to unpack Agent result: bad archive") == (
        "technical",
        "execution_error",
    )


def test_other_process_exit_codes_are_unknown_execution_error():
    assert classify_failure(2, "Agent process exited 2") == ("unknown", "execution_error")


def test_unrecognized_failures_fall_back_to_unknown():
    assert classify_failure(1, "something completely unexpected happened") == (
        "unknown",
        "unknown",
    )
    assert classify_failure(None, "") == ("unknown", "unknown")


def test_rule_priority_review_rejected_beats_stream_pattern():
    assert classify_failure(1, "review_rejected: output was terminated early") == (
        "business",
        "review_rejected",
    )


def test_rule_priority_timeout_beats_generic_exit():
    assert classify_failure(124, "Agent process exited 124") == ("technical", "timeout")


def test_skill_validator_rejection_is_business():
    assert classify_failure(
        1,
        "content review rejected by skill v1.0.2 validator: "
        "review_status/recommendation did not pass",
    ) == ("business", "review_rejected")


def test_timed_out_message_is_timeout():
    assert classify_failure(1, "openclaw command timed out") == ("technical", "timeout")
    assert classify_failure(1, "timed out") == ("technical", "timeout")


def test_executor_not_registered_is_technical():
    assert classify_failure(None, "Executor 'pi' is not registered") == (
        "technical",
        "executor_unregistered",
    )


def test_worker_interrupted_and_lease_expired_are_worker_orphaned():
    assert classify_failure(-1, "worker interrupted before restart") == (
        "technical",
        "worker_orphaned",
    )
    assert classify_failure(None, "lease expired") == ("technical", "worker_orphaned")


def test_disk_full_is_resource_limit():
    assert classify_failure(1, "[Errno 28] No space left on device: '/data/jobs/x'") == (
        "technical",
        "resource_limit",
    )
    assert classify_failure(1, "No space left on device") == (
        "technical",
        "resource_limit",
    )


def test_output_validation_rejected_is_business():
    assert classify_failure(
        1, "Output validation failed: Review rejected key_info items: ki_04858a24"
    ) == ("business", "review_rejected")


def test_missing_outputs_after_pi_run_is_output_missing():
    assert classify_failure(1, "Missing outputs after Pi run: key_info_raw.json") == (
        "technical",
        "output_missing",
    )
    assert classify_failure(1, "missing outputs: subtitles_reviewed.srt") == (
        "technical",
        "output_missing",
    )
    assert classify_failure(1, "Missing required file: subtitle_review_report.json") == (
        "technical",
        "output_missing",
    )


def test_heartbeat_expired_is_worker_orphaned():
    assert classify_failure(None, "Agent Worker heartbeat expired") == (
        "technical",
        "worker_orphaned",
    )


def test_stale_agent_definition_is_technical():
    assert classify_failure(
        None,
        "Agent definition 'generator-v1' was disabled or changed while the request was queued",
    ) == ("technical", "stale_definition")


def test_unresolved_manifest_model_is_technical():
    # 旧 guard 文案（agent 配置治理前）与现文案（provider/model 对）都归一类。
    assert classify_failure(
        None,
        "Agent request manifest has unresolved model '': no Worker could ever claim it; "
        "declare the model via the node/agent execution settings",
    ) == ("technical", "unresolved_model")
    assert classify_failure(
        None,
        "Agent request manifest has unresolved provider/model 'gateway'/'': no Worker "
        "could ever claim it; declare them via the node execution settings or the "
        "workspace defaults",
    ) == ("technical", "unresolved_model")


def test_http_502_is_provider_request():
    assert classify_failure(
        1, "HTTPError: 502 Server Error: Bad Gateway for url: http://account.internal/x"
    ) == ("technical", "provider_request")


def test_resolve_failure_fields_only_classifies_failed_runs():
    assert resolve_failure_fields("completed", 0, "") == ("", "")
    assert resolve_failure_fields("cancelled", 1, "terminated") == ("", "")


def test_resolve_failure_fields_prefers_declared_classification():
    assert resolve_failure_fields(
        "failed", None, "orphaned recovery", "technical", "worker_orphaned"
    ) == (
        "technical",
        "worker_orphaned",
    )


def test_resolve_failure_fields_classifies_when_undeclared():
    assert resolve_failure_fields("failed", 1, "terminated") == (
        "technical",
        "provider_stream",
    )


def test_output_validation_failure_is_business_output_invalid():
    assert classify_failure(
        1, "Output validation failed: related_key_info_id must be 'ki_{uuid}'"
    ) == ("business", "output_invalid")
    assert classify_failure(
        1,
        "Output validation failed: review_result.json: content review did not pass: "
        "review_status='pending_review'",
    ) == ("business", "output_invalid")


def test_validator_environment_failure_is_technical_validator_env():
    """The validator itself failing to run (ro skills mount, missing skill)
    is an environment fault, distinct from the output failing the contract."""
    assert classify_failure(
        1,
        "Validator error: [Errno 30] Read-only file system: "
        "'/root/.agents/skills/agent-legion/x/y.lock'",
    ) == ("technical", "validator_env")


def test_invalid_json_output_is_business_output_invalid():
    assert classify_failure(
        1, "invalid json interactions.json: Expecting ',' delimiter: line 15 column 17"
    ) == ("business", "output_invalid")


def test_interaction_contract_violation_is_business_output_invalid():
    assert classify_failure(1, "Interaction 1 is missing 'id'") == (
        "business",
        "output_invalid",
    )
    assert classify_failure(1, "Interaction 1 ('a7b9c3d2') is missing 'instruction'") == (
        "business",
        "output_invalid",
    )
    assert classify_failure(
        1, "Interaction 1 has unknown type ''. Expected one of: example_practice"
    ) == ("business", "output_invalid")


def test_transcription_provider_failure_is_business():
    # Agreed with operators: all transcription failures are source-data problems.
    assert classify_failure(
        1,
        "All transcription providers failed: whisper: subtitle entry too long: 65.8s; "
        "sensevoice: subtitle entry too long: 62.6s",
    ) == ("business", "transcription_input")
    assert classify_failure(
        1,
        "RuntimeError: All transcription providers failed: whisper: whisper error: "
        "whisper binary not found: whisper-cli",
    ) == ("business", "transcription_input")


def test_http_404_is_business_source_missing():
    assert classify_failure(
        1, "HTTPError: 404 Client Error: Not Found for url: https://cdn.example/x.mp4"
    ) == ("business", "source_missing")


def test_unresolvable_intake_inputs_are_business_source_missing():
    assert classify_failure(1, "RuntimeError: knowledge video not found: K404") == (
        "business",
        "source_missing",
    )
    assert classify_failure(1, "RuntimeError: no questions found for knowledge code: K404") == (
        "business",
        "source_missing",
    )


def test_cancelled_execution_is_technical_cancelled():
    assert classify_failure(None, "execution was cancelled") == ("technical", "cancelled")


def test_executor_binding_failures_are_technical():
    assert classify_failure(None, "No Executor binding") == ("technical", "executor_binding")
    assert classify_failure(None, "workspace node is not routed to an Agent") == (
        "technical",
        "executor_binding",
    )


def test_skill_setup_failures_are_technical_skill_config():
    assert classify_failure(
        None,
        "skill 'video_knowledge/review_subtitles' config differs from the published skill lock",
    ) == ("technical", "skill_config")
    assert classify_failure(
        None, "skill missing references/output-contract.md: 'video_knowledge/review_subtitles'"
    ) == ("technical", "skill_config")
    assert classify_failure(None, "git command failed: git -C /skills/x checkout abc123") == (
        "technical",
        "skill_config",
    )


def test_artifact_transfer_failures_are_technical():
    assert classify_failure(None, "artifact upload failed: HTTP 500: b'Internal Server Error'") == (
        "technical",
        "artifact_transfer",
    )
    assert classify_failure(None, "download failed: /api/agent-executions/e1/bundle: HTTP 500") == (
        "technical",
        "artifact_transfer",
    )


def test_lease_lost_is_technical():
    assert classify_failure(None, "lease was lost during execution") == (
        "technical",
        "lease_lost",
    )


def test_sqlite_legacy_errors_are_technical_database():
    assert classify_failure(None, "database is locked") == ("technical", "database")
    assert classify_failure(None, "cannot rollback - no transaction is active") == (
        "technical",
        "database",
    )
    assert classify_failure(None, "unable to open database file") == ("technical", "database")
    assert classify_failure(None, "database or disk is full") == ("technical", "database")


def test_network_transfer_errors_are_technical_network():
    assert classify_failure(
        None, "('Connection broken: IncompleteRead(11304257 bytes read)', IncompleteRead(...))"
    ) == ("technical", "network")
    assert classify_failure(None, "ChunkedEncodingError: ('Connection broken', ...)") == (
        "technical",
        "network",
    )


def test_legacy_process_failures_are_technical_execution_error():
    assert classify_failure(1, "openclaw command failed") == ("technical", "execution_error")
    assert classify_failure(
        1, "SQLite objects created in a thread can only be used in that same thread"
    ) == ("technical", "execution_error")
    assert classify_failure(1, "isolated handler did not return a result") == (
        "technical",
        "execution_error",
    )
    assert classify_failure(1, "'str' object has no attribute 'get'") == (
        "technical",
        "execution_error",
    )
    assert classify_failure(1, "[Errno 2] No such file or directory: '/data/run/x'") == (
        "technical",
        "execution_error",
    )


def test_pool_timeout_is_transient_technical():
    category, detail = classify_failure(1, "PoolTimeout: couldn't get a connection after 10.00 sec")
    assert category == "technical"
    assert detail == "db_pool_timeout"
    assert is_transient_retryable(detail)


def test_bare_pool_connection_timeout_is_transient_technical():
    category, detail = classify_failure(1, "couldn't get a connection after 10.00 sec")
    assert category == "technical"
    assert detail == "db_pool_timeout"
    assert is_transient_retryable(detail)


def test_velites_transient_http_502_is_provider_request():
    assert classify_failure(0, "provider call failed (transient): HTTP 502: ") == (
        "technical",
        "provider_request",
    )


def test_velites_deterministic_call_failure_is_provider_request():
    assert classify_failure(0, "provider call failed: HTTP 400: invalid request") == (
        "technical",
        "provider_request",
    )


def test_velites_transport_interruptions_are_provider_stream():
    assert classify_failure(
        0,
        "provider call failed (transient): transport error: error decoding response body "
        "(error reading a body from connection <== unexpected EOF during chunk size line)",
    ) == ("technical", "provider_stream")
    assert classify_failure(0, "provider call failed: stream error: 操作失败") == (
        "technical",
        "provider_stream",
    )


def test_velites_sensitive_finish_reason_is_business():
    # The provider's content filter stopped generation — retrying unchanged
    # infrastructure will not help, so this is business like review_rejected.
    assert classify_failure(0, "provider call failed: unexpected finish_reason `sensitive`") == (
        "business",
        "provider_content_filter",
    )


def test_non_transient_details_are_not_retryable():
    assert not is_transient_retryable("provider_stream")
    assert not is_transient_retryable("database")
    assert not is_transient_retryable("")
