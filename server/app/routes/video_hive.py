from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..settings import Settings


class AsrConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    whisper_configured: bool = Field(alias="whisperConfigured")
    sensevoice_configured: bool = Field(alias="sensevoiceConfigured")
    vad_enabled: bool = Field(alias="vadEnabled")


class OpenclawConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    runner_count: int = Field(alias="runnerCount")
    timeout_seconds: int = Field(alias="timeoutSeconds")


class VideoHiveConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    asr: AsrConfigResponse
    openclaw: OpenclawConfigResponse


def create_video_hive_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/video-hive")

    @router.get(
        "/config",
        response_model=VideoHiveConfigResponse,
        summary="Get Agent Legion video pipeline config",
    )
    def get_video_hive_config() -> VideoHiveConfigResponse:
        asr = settings.config.get("asr", {}) or {}
        whisper = asr.get("whisper", {}) or {}
        sensevoice = asr.get("sensevoice", {}) or {}
        openclaw = settings.config.get("openclaw", {}) or {}
        runners = openclaw.get("runners", [])

        whisper_configured = bool(whisper.get("binary") and whisper.get("model"))
        sensevoice_configured = bool(sensevoice.get("script") and sensevoice.get("model_dir"))
        vad_enabled = bool(whisper.get("vad_model"))

        return VideoHiveConfigResponse(
            asr=AsrConfigResponse.model_construct(
                provider=str(asr.get("provider", "auto")),
                whisper_configured=whisper_configured,
                sensevoice_configured=sensevoice_configured,
                vad_enabled=vad_enabled,
            ),
            openclaw=OpenclawConfigResponse.model_construct(
                runner_count=len(runners) if isinstance(runners, list) else 0,
                timeout_seconds=int(openclaw.get("timeout_seconds", 600)),
            ),
        )

    return router
