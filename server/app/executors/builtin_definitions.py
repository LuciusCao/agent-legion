"""Built-in executor definitions (retired ``config/workflow.yaml`` executors section).

Executor definitions are versioned entities (``versioned_entities``,
``entity_type='executor'``) managed in Studio and hydrated from the DB at
startup. This module pins the factory catalog: the seed path
(``executor_definition_service.seed_builtin_executor_definitions``) publishes
these definitions when an executor has no published row yet, so existing
deployments keep running unchanged and admin edits are never overwritten.

The raw shape below feeds ``load_executor_definitions`` directly; keep it
field-for-field equivalent to the last tracked yaml.
"""

from __future__ import annotations

from typing import Any

# CMS connection schema shared by the capabilities that call the CMS: the
# instance-level external connection key plus non-secret business selectors.
# Endpoint URLs and credentials live on the connection (admin settings →
# 外部服务连接); values resolve along schema defaults → node config →
# workspace override, and the connection config + token are injected in
# memory at dispatch (VAULT-SECRET-001).
_CONNECTION_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "cms-internal",
    "description": "外部服务连接 key（admin 全局设置「外部服务连接」中维护；出厂默认值，可被节点/workspace 覆盖）",
}
_BANK_VERSION_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "v5",
    "description": "CMS 题库版本（出厂默认值，可被节点/workspace 覆盖）",
}
_COUNTRY_ID_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "1",
    "description": "CMS 国家/地区 ID（出厂默认值，可被节点/workspace 覆盖）",
}
_SUBJECT_ID_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "2",
    "description": "CMS 学科 ID（出厂默认值，可被节点/workspace 覆盖）",
}

_FETCH_QUESTIONS_PROPERTIES: dict[str, Any] = {
    "connection": _CONNECTION_PROPERTY,
    "bank_version": _BANK_VERSION_PROPERTY,
    "country_id": _COUNTRY_ID_PROPERTY,
    "subject_id": _SUBJECT_ID_PROPERTY,
    "page_size": {
        "type": "integer",
        "default": 50,
        "minimum": 1,
        "maximum": 500,
        "description": "知识点题目列表分页大小（出厂默认值，可被节点/workspace 覆盖）",
    },
}

_DOWNLOAD_VIDEO_PROPERTIES: dict[str, Any] = {
    "connection": _CONNECTION_PROPERTY,
    "bank_version": _BANK_VERSION_PROPERTY,
    "country_id": _COUNTRY_ID_PROPERTY,
    "subject_id": _SUBJECT_ID_PROPERTY,
}

BUILTIN_EXECUTOR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "code-default": {
        "kind": "code",
        "global_capacity": 16,
        "capabilities": {
            # Custom forks call the CMS: allow network inside the sandbox
            # (EXEC-CODE-003).
            "fetch_questions": {
                "path": "workflow_nodes/question_intake.py",
                "sandbox_network": True,
                "config_schema": {
                    "type": "object",
                    "properties": _FETCH_QUESTIONS_PROPERTIES,
                },
            },
            # Custom forks call the CMS / video source: allow network in the
            # sandbox; same instance-level connection injection (endpoint
            # config + token resolved in memory at dispatch) and factory
            # defaults as fetch_questions (page_size is not used by this
            # node).
            "download_video": {
                "path": "workflow_nodes/video_download.py",
                "sandbox_network": True,
                "config_schema": {
                    "type": "object",
                    "properties": _DOWNLOAD_VIDEO_PROPERTIES,
                },
            },
            "clean_and_parse": {
                "path": "workflow_nodes/question_clean_parse.py",
            },
            "classify_comprehension_eligibility": {
                "path": "workflow_nodes/comprehension_classify.py",
            },
            "finalize_non_uploadable": {
                "path": "workflow_nodes/comprehension_finalize.py",
            },
            "assemble_comprehension_info": {
                "path": "workflow_nodes/comprehension_assemble.py",
            },
            # ASR providers are local subprocesses (whisper-cli / the SenseVoice
            # funasr script); SenseVoice may download models on first run, so
            # allow network in the sandbox. The retired yaml ``asr:`` section's
            # business parameters live in this config_schema (factory defaults,
            # overridable per node/workspace); the machine-local binary/model
            # paths are env-only (AGENT_LEGION_ASR_*).
            "transcribe_video": {
                "path": "workflow_nodes/video_transcribe.py",
                "sandbox_network": True,
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "enum": ["auto", "whisper", "sensevoice"],
                            "default": "auto",
                            "description": "ASR 提供方选择（出厂默认值，可被节点/workspace 覆盖）",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 900,
                            "description": "单次转写超时秒数（出厂默认值，可被节点/workspace 覆盖）",
                        },
                    },
                },
            },
            "assemble_video_metadata": {
                "path": "workflow_nodes/video_assemble.py",
            },
            "package_video_job": {
                "path": "workflow_nodes/video_package.py",
            },
        },
    },
}
