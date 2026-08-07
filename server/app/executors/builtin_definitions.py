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

# CMS connection schema shared by the capabilities that call the CMS: non-secret
# selectors plus the vault-backed token (secret: true). Values resolve along
# schema defaults → node config → workspace override; the schema defaults are
# the product factory values (the global yaml cms: section was retired), and
# base_url comes from node/workspace config or env CMS_BASE_URL. The token is
# stored Fernet-encrypted in the workspace vault and injected in memory at
# dispatch (VAULT-SECRET-001).
_TOKEN_PROPERTY: dict[str, Any] = {
    "type": "string",
    "secret": True,
    "description": "CMS 访问 token（存入 workspace vault，只写不读）",
}
_ENV_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "prod",
    "description": "CMS 环境标识（prod 时启用服务端 token 生成）",
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
    "base_url": {
        "type": "string",
        "description": "CMS 服务根地址（用于派生各端点 URL；缺省由 env CMS_BASE_URL 提供）",
    },
    "api_url": {
        "type": "string",
        "description": "题目详情端点完整 URL（优先于 base_url 派生）",
    },
    "question_list_url": {
        "type": "string",
        "description": "知识点题目列表端点完整 URL（优先于 base_url 派生）",
    },
    "token": _TOKEN_PROPERTY,
    "env": _ENV_PROPERTY,
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
    "base_url": {
        "type": "string",
        "description": "CMS 服务根地址（用于派生端点 URL；缺省由 env CMS_BASE_URL 提供）",
    },
    "api_url": {
        "type": "string",
        "description": "知识点详情端点完整 URL（优先于 base_url 派生）",
    },
    "token": _TOKEN_PROPERTY,
    "env": _ENV_PROPERTY,
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
            # sandbox; same vault-backed token semantics and factory defaults
            # as fetch_questions (page_size is not used by this node).
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
