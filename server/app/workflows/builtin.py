"""Built-in workflow DAG definitions (product factory defaults).

These constants replace the retired ``config/workflows/*.yaml`` files. They are
validated through the same loader used for Studio draft payloads; binding a
workspace publishes them as per-workspace DB revisions.

Keep this module dependency-minimal (workflow definition models only): scripts
import it and it must not pull in settings or database access.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION, DEMO_WORKFLOW_KEY
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.loader import workflow_definition_from_dict

BUILTIN_WORKFLOW_DEFINITIONS: dict[str, dict[str, Any]] = {
    DEMO_WORKFLOW_KEY: DEMO_WORKFLOW_DEFINITION,
    "question_comprehension_info": {
        "key": "question_comprehension_info",
        "label": "题目审题信息生成 DAG",
        "schema_version": 2,
        "intake": {
            "modes": {
                "batch_by_knowledge": {
                    "label": "按知识点批量",
                    "input_field": "knowledge_codes",
                },
                "batch_by_ids": {
                    "label": "按题目ID批量",
                    "input_field": "question_ids",
                },
            },
        },
        "nodes": {
            "fetch_questions": {
                "label": "获取题目",
                "capability": "fetch_questions",
                "after": [],
                "inputs": [],
                "outputs": ["questions.json"],
            },
            "clean_and_parse": {
                "label": "清洗与解析",
                "capability": "clean_and_parse",
                "after": ["fetch_questions"],
                "inputs": ["questions.json"],
                "outputs": ["questions_parsed.json", "questions_parsed_lean.json"],
            },
            "classify_comprehension_eligibility": {
                "label": "判断是否适合审题",
                "capability": "classify_comprehension_eligibility",
                "inputs": ["questions_parsed.json"],
                "outputs": ["comprehension_eligibility.json"],
            },
            "generate_key_info": {
                "label": "生成关键信息",
                "capability": "generate_key_info",
                "after": ["clean_and_parse"],
                "inputs": ["questions_parsed_lean.json"],
                "outputs": ["key_info_raw.json", "key_info_report.json"],
            },
            "review_key_info": {
                "label": "审核关键信息",
                "capability": "review_key_info",
                "after": ["generate_key_info"],
                "inputs": ["questions_parsed.json", "key_info_raw.json"],
                "outputs": [
                    "key_info_reviewed.json",
                    "key_info_reviewed_lean.json",
                    "key_info_review_report.json",
                ],
            },
            "generate_possible_errors": {
                "label": "生成可能审题错误",
                "capability": "generate_possible_errors",
                "after": ["review_key_info"],
                "inputs": ["questions_parsed_lean.json", "key_info_reviewed_lean.json"],
                "outputs": ["possible_errors_raw.json", "possible_errors_report.json"],
            },
            "review_possible_errors": {
                "label": "审核可能审题错误",
                "capability": "review_possible_errors",
                "after": ["generate_possible_errors"],
                "inputs": [
                    "questions_parsed_lean.json",
                    "key_info_reviewed_lean.json",
                    "possible_errors_raw.json",
                ],
                "outputs": [
                    "possible_errors_reviewed.json",
                    "possible_errors_review_report.json",
                ],
            },
            "assess_comprehension_difficulty": {
                "label": "评估审题难度",
                "capability": "assess_comprehension_difficulty",
                "after": ["review_key_info", "review_possible_errors"],
                "inputs": [
                    "questions_parsed_lean.json",
                    "key_info_reviewed_lean.json",
                    "possible_errors_reviewed.json",
                ],
                "outputs": [
                    "comprehension_difficulty.json",
                    "comprehension_difficulty_report.json",
                ],
            },
            "assemble_comprehension_info": {
                "label": "组装审题信息",
                "capability": "assemble_comprehension_info",
                "after": ["assess_comprehension_difficulty"],
                "inputs": [
                    "questions_parsed_lean.json",
                    "key_info_reviewed.json",
                    "possible_errors_reviewed.json",
                    "comprehension_difficulty.json",
                ],
                "outputs": ["comprehension_info.json", "manifest.json"],
                "terminal": {"outcome": "uploadable"},
            },
            "finalize_non_uploadable": {
                "label": "结束：不上传",
                "capability": "finalize_non_uploadable",
                "inputs": ["questions_parsed.json", "comprehension_eligibility.json"],
                "outputs": ["manifest.json"],
                "terminal": {"outcome": "non_uploadable"},
            },
        },
        "edges": [
            {"from": "fetch_questions", "to": "clean_and_parse"},
            {"from": "clean_and_parse", "to": "classify_comprehension_eligibility"},
            {
                "from": "classify_comprehension_eligibility",
                "to": "generate_key_info",
                "when": {
                    "artifact": "comprehension_eligibility.json",
                    "path": "$.eligible",
                    "equals": True,
                },
            },
            {
                "from": "classify_comprehension_eligibility",
                "to": "finalize_non_uploadable",
                "when": {
                    "artifact": "comprehension_eligibility.json",
                    "path": "$.eligible",
                    "equals": False,
                },
            },
            {"from": "generate_key_info", "to": "review_key_info"},
            {"from": "review_key_info", "to": "generate_possible_errors"},
            {"from": "review_key_info", "to": "assess_comprehension_difficulty"},
            {"from": "generate_possible_errors", "to": "review_possible_errors"},
            {"from": "review_possible_errors", "to": "assess_comprehension_difficulty"},
            {"from": "assess_comprehension_difficulty", "to": "assemble_comprehension_info"},
        ],
    },
    "video_knowledge": {
        "key": "video_knowledge",
        "label": "知识视频 DAG",
        "schema_version": 2,
        "intake": {
            "modes": {
                "batch_by_urls": {
                    "label": "按视频链接批量",
                    "input_field": "video_urls",
                },
                "batch_by_knowledge": {
                    "label": "按知识点批量",
                    "input_field": "knowledge_codes",
                },
            },
        },
        "nodes": {
            "download": {
                "label": "下载视频",
                "capability": "download_video",
                "after": [],
                "inputs": ["video_input.json"],
                "outputs": ["source.mp4", "video_input.json"],
            },
            "transcribe": {
                "label": "生成字幕",
                "capability": "transcribe_video",
                "after": ["download"],
                "inputs": ["source.mp4", "video_input.json"],
                "outputs": ["subtitles.srt", "transcription.json"],
            },
            "subtitle_review": {
                "label": "审核字幕",
                "capability": "review_subtitles",
                "after": ["transcribe"],
                "inputs": ["subtitles.srt", "transcription.json", "video_input.json"],
                "outputs": ["subtitles_reviewed.srt", "subtitle_review_report.json"],
            },
            "chapter_generate": {
                "label": "生成章节",
                "capability": "generate_chapters",
                "after": ["subtitle_review"],
                "inputs": ["subtitles_reviewed.srt", "video_input.json"],
                "outputs": ["chapters_raw.json", "chapters.json"],
            },
            "interaction_generate": {
                "label": "生成互动题",
                "capability": "generate_interactions",
                "after": ["chapter_generate"],
                "inputs": ["subtitles_reviewed.srt", "chapters.json", "video_input.json"],
                "outputs": ["interactions.json"],
            },
            "content_review": {
                "label": "审核内容",
                "capability": "review_video_content",
                "after": ["interaction_generate"],
                "inputs": [
                    "subtitles_reviewed.srt",
                    "chapters.json",
                    "interactions.json",
                    "video_input.json",
                ],
                "outputs": ["checklist.json", "review_result.json"],
            },
            "assemble": {
                "label": "组装元数据",
                "capability": "assemble_video_metadata",
                "after": ["content_review"],
                "inputs": [
                    "video_input.json",
                    "source.mp4",
                    "subtitles.srt",
                    "subtitles_reviewed.srt",
                    "chapters.json",
                    "interactions.json",
                    "checklist.json",
                    "review_result.json",
                ],
                "outputs": ["metadata.json", "report.md", "upload_params.json"],
            },
            "package": {
                "label": "打包视频",
                "capability": "package_video_job",
                "after": ["assemble"],
                "inputs": ["metadata.json", "report.md", "upload_params.json"],
                "outputs": ["package_manifest.json"],
            },
        },
        "edges": [
            {"from": "download", "to": "transcribe"},
            {"from": "transcribe", "to": "subtitle_review"},
            {"from": "subtitle_review", "to": "chapter_generate"},
            {"from": "chapter_generate", "to": "interaction_generate"},
            {"from": "interaction_generate", "to": "content_review"},
            {"from": "content_review", "to": "assemble"},
            {"from": "assemble", "to": "package"},
        ],
    },
}


def load_builtin_workflow(workflow_key: str) -> WorkflowDefinition:
    """Load and validate a built-in workflow definition; KeyError for unknown keys."""
    raw = BUILTIN_WORKFLOW_DEFINITIONS.get(workflow_key)
    if raw is None:
        raise KeyError(workflow_key)
    return workflow_definition_from_dict(raw)


def list_builtin_workflows() -> list[WorkflowDefinition]:
    """Load and validate every built-in workflow definition."""
    return [load_builtin_workflow(key) for key in BUILTIN_WORKFLOW_DEFINITIONS]
