from server.app.services.failure_classification import (
    FAILURE_CATEGORIES,
    classify_failure,
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


def test_resource_limit_errors():
    assert classify_failure(1, "OSError: [Errno 24] Too many open files") == (
        "technical",
        "resource_limit",
    )
    assert classify_failure(1, "Too many open files") == ("technical", "resource_limit")


def test_missing_outputs_is_output_missing():
    assert classify_failure(1, "Missing outputs: key_info.json") == (
        "unknown",
        "output_missing",
    )
    assert classify_failure(1, "Agent Worker did not report output artifacts") == (
        "unknown",
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
