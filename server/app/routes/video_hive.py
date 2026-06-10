from fastapi import APIRouter
from pydantic import BaseModel

from ..settings import Settings


class AsrConfigResponse(BaseModel):
    provider: str
    whisperConfigured: bool
    sensevoiceConfigured: bool
    vadEnabled: bool


class OpenclawConfigResponse(BaseModel):
    runnerCount: int
    timeoutSeconds: int


class VideoHiveConfigResponse(BaseModel):
    asr: AsrConfigResponse
    openclaw: OpenclawConfigResponse


def create_video_hive_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/video-hive")

    @router.get("/config", response_model=VideoHiveConfigResponse)
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
            asr=AsrConfigResponse(
                provider=str(asr.get("provider", "auto")),
                whisperConfigured=whisper_configured,
                sensevoiceConfigured=sensevoice_configured,
                vadEnabled=vad_enabled,
            ),
            openclaw=OpenclawConfigResponse(
                runnerCount=len(runners) if isinstance(runners, list) else 0,
                timeoutSeconds=int(openclaw.get("timeout_seconds", 600)),
            ),
        )

    return router
