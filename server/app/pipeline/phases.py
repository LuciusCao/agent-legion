from pathlib import Path

from server.app.pipeline.openclaw import AgentPhase

PHASES = [
    "download",
    "transcribe",
    "subtitle_review",
    "chapter_generate",
    "interaction_generate",
    "content_review",
    "assemble",
]


AGENT_PHASES = {
    "subtitle_review": AgentPhase(
        key="subtitle_review",
        reference_path=Path("server/app/pipeline/references/phase-03-subtitle-review.md"),
        expected_outputs=["subtitles_reviewed.srt", "subtitle_review_report.json"],
        json_outputs=["subtitle_review_report.json"],
    ),
    "chapter_generate": AgentPhase(
        key="chapter_generate",
        reference_path=Path("server/app/pipeline/references/phase-04-chapter-generate.md"),
        expected_outputs=["chapters_raw.json", "chapters.json"],
        json_outputs=["chapters_raw.json", "chapters.json"],
    ),
    "interaction_generate": AgentPhase(
        key="interaction_generate",
        reference_path=Path("server/app/pipeline/references/phase-05-interaction-generate.md"),
        expected_outputs=["interactions.json"],
        json_outputs=["interactions.json"],
    ),
    "content_review": AgentPhase(
        key="content_review",
        reference_path=Path("server/app/pipeline/references/phase-06-content-review.md"),
        expected_outputs=["checklist.json", "review_result.json"],
        json_outputs=["checklist.json", "review_result.json"],
    ),
}


KNOWLEDGE_PHASES = [
    "download",
    "transcribe",
    "subtitle_review",
    "chapter_generate",
    "interaction_generate",
    "content_review",
    "assemble",
]

QUESTION_PHASES = [
    "download",
    "transcribe",
    "subtitle_review",
    "chapter_generate",
    "assemble",
]


def phase_sequence(content_type: str) -> list[str]:
    return QUESTION_PHASES if content_type == "question" else KNOWLEDGE_PHASES


def next_phase(phase: str, content_type: str = "knowledge") -> str | None:
    phases = phase_sequence(content_type)
    index = phases.index(phase)
    if index + 1 >= len(phases):
        return None
    return phases[index + 1]
