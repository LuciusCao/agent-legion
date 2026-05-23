from typing import TypedDict


class VideoRecord(TypedDict):
    id: str
    source_url: str
    title: str
    content_type: str
    external_id: str
    knowledge_code: str
    question_id: str
    storage_dir: str
    current_phase: str
    status: str
    duration: float
    error_message: str
    created_at: str
    updated_at: str


class PhaseRunRecord(TypedDict):
    id: int
    video_id: str
    phase_key: str
    status: str
    started_at: str
    finished_at: str | None
    command_json: str
    exit_code: int | None
    log_path: str
    error_message: str


VIDEO_RECORD_FIELDS = set(VideoRecord.__annotations__)
PHASE_RUN_FIELDS = set(PhaseRunRecord.__annotations__)
