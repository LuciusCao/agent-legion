from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from server.app.agent_stock import AgentStockConfig
from server.app.cms.auth import cms_token_available
from server.app.executors.config import ExecutorConfig, PiExecutorConfig


class PiRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Headless harness 选择（设计文档 velites-harness.md §9）；非法值 fail-fast。
    flavor: Literal["pi", "velites"] = "pi"
    binary: str = Field(default="pi", validate_default=True)
    provider: str = ""
    model: str = ""
    thinking: str = ""
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("binary")
    @classmethod
    def _flavor_binary(cls, value: str, info: ValidationInfo) -> str:
        return "velites" if info.data.get("flavor") == "velites" and value == "pi" else value


class OpenClawSkillSafetyRepo(BaseModel):
    """One skill checkout the OpenClaw runner may force-restore before a run.

    Only the path is declared here; the restore ref is pinned by
    ``config/skills.lock`` (config governance G3, single source of truth).
    A ``ref`` key is rejected as an extra field.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class OpenClawSkillSafetyRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    repos: list[OpenClawSkillSafetyRepo] = Field(default_factory=list)


class OpenClawRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command_template: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=600, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    isolated_workspace_root: str = ""
    skill_safety: OpenClawSkillSafetyRuntimeConfig = Field(
        default_factory=OpenClawSkillSafetyRuntimeConfig
    )


class WorkflowsRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    pi: PiRuntimeConfig = Field(default_factory=PiRuntimeConfig)


class AgentWorkersRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    register_token: str = ""
    register_token_file: str = ""
    max_archive_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    min_protocol_version: int = Field(default=1, ge=1)


class ExecutorRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heartbeat_interval_seconds: float = Field(default=10, gt=0)
    lease_ttl_seconds: int = Field(default=90, ge=1)
    heartbeat_failure_threshold: int = Field(default=3, ge=1)
    cancellation_grace_seconds: int = Field(default=5, ge=0)
    sweeper_enabled: bool = True
    sweeper_interval_seconds: float = Field(default=5.0, gt=0)
    workflows: WorkflowsRuntimeConfig = Field(default_factory=WorkflowsRuntimeConfig)
    openclaw: OpenClawRuntimeConfig = Field(
        default_factory=lambda: OpenClawRuntimeConfig(command_template=("openclaw",))
    )
    agent_workers: AgentWorkersRuntimeConfig = Field(default_factory=AgentWorkersRuntimeConfig)
    agent_stock: AgentStockConfig = Field(default_factory=AgentStockConfig)


class StartupValidationError(Exception):
    """Aggregated startup configuration errors.

    Diagnostics list field paths and human-readable problems; secret values are
    never included so messages can be logged safely.
    """

    def __init__(self, fields: list[tuple[str, str]]) -> None:
        self.fields = fields
        super().__init__(self._format(fields))

    @staticmethod
    def _format(fields: list[tuple[str, str]]) -> str:
        return "Startup validation failed: " + "; ".join(
            f"{location}: {message}" for location, message in fields
        )


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


def _resolve_executable(value: str) -> Path | None:
    expanded = os.path.expanduser(value)
    if os.sep in expanded or (os.altsep and os.altsep in expanded):
        path = Path(expanded)
        return path if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(expanded)
    return Path(found) if found else None


def _cms_resource_enabled(config: dict[str, Any]) -> bool:
    """Return True when a CMS-backed resource provider is enabled."""
    providers = config.get("resource_providers")
    if not isinstance(providers, dict):
        return False
    for entry in providers.values():
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", ""))
        if provider.startswith("cms.") and entry.get("enabled") is not False:
            return True
    return any(
        str(key).startswith("cms.")
        and isinstance(entry, dict)
        and entry.get("enabled") is not False
        for key, entry in providers.items()
    )


def validate_runtime(
    runtime: ExecutorRuntimeConfig,
    config: dict[str, Any],
    executor_definitions: Mapping[str, ExecutorConfig] | None = None,
) -> None:
    """Validate enabled runtime dependencies at startup.

    Disabled runtimes require nothing. Enabled executors require their executable
    or working directory to exist. Selected ASR providers require their files;
    ``auto`` needs at least one usable provider. CMS credentials are required only
    when a CMS-backed resource provider is enabled.
    """
    errors: list[tuple[str, str]] = []

    asr_config = config.get("asr") or {}
    if not isinstance(asr_config, dict):
        asr_config = {}
    provider = str(asr_config.get("provider", "auto")).lower()
    whisper_cfg = asr_config.get("whisper") or {}
    if not isinstance(whisper_cfg, dict):
        whisper_cfg = {}
    sensevoice_cfg = asr_config.get("sensevoice") or {}
    if not isinstance(sensevoice_cfg, dict):
        sensevoice_cfg = {}

    def _whisper_usable() -> bool:
        binary = str(whisper_cfg.get("binary", ""))
        model = str(whisper_cfg.get("model", ""))
        if not binary or not model:
            return False
        model_path = _expand(model)
        return _resolve_executable(binary) is not None and model_path.is_file()

    def _sensevoice_usable() -> bool:
        model_dir = str(sensevoice_cfg.get("model_dir", ""))
        script = str(sensevoice_cfg.get("script", ""))
        if not model_dir:
            return False
        model_dir_path = _expand(model_dir)
        script_path = _expand(script) if script else None
        if not model_dir_path.is_dir():
            return False
        return script_path is None or script_path.is_file()

    if provider == "whisper":
        binary = str(whisper_cfg.get("binary", ""))
        model = str(whisper_cfg.get("model", ""))
        if not binary:
            errors.append(("asr.whisper.binary", "missing whisper binary"))
        elif _resolve_executable(binary) is None:
            errors.append(("asr.whisper.binary", "whisper binary is not executable or on PATH"))
        if not model:
            errors.append(("asr.whisper.model", "missing whisper model"))
        elif not _expand(model).is_file():
            errors.append(("asr.whisper.model", "whisper model does not exist"))

    if provider == "sensevoice":
        model_dir = str(sensevoice_cfg.get("model_dir", ""))
        script = str(sensevoice_cfg.get("script", ""))
        if not model_dir:
            errors.append(("asr.sensevoice.model_dir", "missing sensevoice model_dir"))
        elif not _expand(model_dir).is_dir():
            errors.append(("asr.sensevoice.model_dir", "sensevoice model_dir does not exist"))
        if script and not _expand(script).is_file():
            errors.append(("asr.sensevoice.script", "sensevoice script does not exist"))

    if provider == "auto" and not (_whisper_usable() or _sensevoice_usable()):
        errors.append(("asr.provider", "auto mode requires at least one usable ASR provider"))

    if runtime.workflows.enabled and any(
        isinstance(definition, PiExecutorConfig)
        for definition in (executor_definitions or {}).values()
    ):
        pi_binary = str(runtime.workflows.pi.binary or "")
        if not pi_binary:
            errors.append(("workflows.pi.binary", "missing pi binary"))
        else:
            if _resolve_executable(pi_binary) is None:
                errors.append(("workflows.pi.binary", "pi binary is not executable or on PATH"))

    openclaw_cwd = str(runtime.openclaw.cwd or ".")
    if not _expand(openclaw_cwd).is_dir():
        errors.append(("openclaw.cwd", "openclaw working directory does not exist"))

    if _cms_resource_enabled(config) and not cms_token_available(config.get("cms")):
        errors.append(
            (
                "cms.token",
                "missing CMS credentials: set env CMS_TOKEN (or "
                "AGENT_LEGION_CMS_TOKEN; BASECMS_TOKEN is a deprecated alias), "
                "set all of CMS_APP_ID / CMS_NONCE / CMS_SECRET / "
                "CMS_TOKEN_URL, or bind a token in the workspace resource "
                "config (vault)",
            )
        )

    if errors:
        raise StartupValidationError(errors)
