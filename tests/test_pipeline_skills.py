import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.skills import resolve_pipeline_skill
from server.app.pipelines.skills.reading_analysis._shared.validation import ContractError

FIXTURE_ROOT = Path("tests/fixtures/reading_analysis/eval")
SKILL_NAMES = [
    "reading_analysis/extract_keywords",
    "reading_analysis/review_keywords",
    "reading_analysis/assess_difficulty",
    "reading_analysis/review_difficulty",
    "reading_analysis/generate_distractors",
    "reading_analysis/review_distractors",
]


def _run_validator(skill_name: str, job_dir: Path) -> list[str]:
    """Call a skill's in-process validate() and return a flat list of error messages."""
    mod = importlib.import_module(
        f"server.app.pipelines.skills.reading_analysis.{skill_name.split('/')[-1]}.scripts.validate_output"
    )
    try:
        result = mod.validate(job_dir)
    except (ContractError, mod.ContractError) as exc:
        return [str(exc)]
    if isinstance(result, list):
        return result
    return []


def _setup_skill_inputs(skill_name: str, job_dir: Path) -> None:
    if skill_name == "reading_analysis/extract_keywords":
        shutil.copy(
            FIXTURE_ROOT / "extract_keywords" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
    elif skill_name == "reading_analysis/review_keywords":
        shutil.copy(
            FIXTURE_ROOT / "review_keywords_valid" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "review_keywords_valid" / "keywords_raw.json",
            job_dir / "keywords_raw.json",
        )
    elif skill_name == "reading_analysis/assess_difficulty":
        shutil.copy(
            FIXTURE_ROOT / "assess_difficulty" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "assess_difficulty" / "keywords_reviewed.json",
            job_dir / "keywords_reviewed.json",
        )
    elif skill_name == "reading_analysis/review_difficulty":
        shutil.copy(
            FIXTURE_ROOT / "review_difficulty_valid" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "review_difficulty_valid" / "keywords_reviewed.json",
            job_dir / "keywords_reviewed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "review_difficulty_valid" / "difficulty_raw.json",
            job_dir / "difficulty_raw.json",
        )
    elif skill_name == "reading_analysis/generate_distractors":
        shutil.copy(
            FIXTURE_ROOT / "generate_distractors" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "generate_distractors" / "keywords_reviewed.json",
            job_dir / "keywords_reviewed.json",
        )
    elif skill_name == "reading_analysis/review_distractors":
        shutil.copy(
            FIXTURE_ROOT / "review_distractors_valid" / "questions_parsed.json",
            job_dir / "questions_parsed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "review_distractors_valid" / "keywords_reviewed.json",
            job_dir / "keywords_reviewed.json",
        )
        shutil.copy(
            FIXTURE_ROOT / "review_distractors_valid" / "distractors_raw.json",
            job_dir / "distractors_raw.json",
        )


def _setup_skill_valid_outputs(skill_name: str, job_dir: Path) -> None:
    if skill_name == "reading_analysis/extract_keywords":
        stem = "小明从上海开车去北京，单程 1400km，往返需要行驶多少千米？"
        source_text = "单程 1400km"
        start = stem.index(source_text)
        end = start + len(source_text)
        (job_dir / "keywords_raw.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "keywords": [
                        {
                            "id": "kw-1",
                            "source_text": source_text,
                            "normalized_text": "单程距离",
                            "location": {
                                "source": "stem",
                                "option_key": None,
                                "start": start,
                                "end": end,
                            },
                            "necessity": "决定路程计算的必要条件",
                            "counterfactual": "删除距离后无法完成数值计算",
                            "confidence": 0.96,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (job_dir / "keywords_report.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "candidate_count": 1,
                    "method": "counterfactual_deletion",
                    "warnings": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif skill_name == "reading_analysis/review_keywords":
        raw_data = json.loads((job_dir / "keywords_raw.json").read_text(encoding="utf-8"))
        (job_dir / "keywords_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )
        sha = hashlib.sha256((job_dir / "keywords_raw.json").read_bytes()).hexdigest()
        (job_dir / "keywords_review_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "question_id": "Q100",
                    "source_artifact": "keywords_raw.json",
                    "source_artifact_sha256": sha,
                    "checks": [],
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif skill_name == "reading_analysis/assess_difficulty":
        (job_dir / "difficulty_raw.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "dimensions": {
                        "knowledge_complexity": 50,
                        "reasoning_steps": 50,
                        "calculation_load": 40,
                        "reading_filter_load": 45,
                    },
                    "weights": {
                        "knowledge_complexity": 0.3,
                        "reasoning_steps": 0.3,
                        "calculation_load": 0.2,
                        "reading_filter_load": 0.2,
                    },
                    "reading_difficulty": 47,
                    "evidence": {
                        "knowledge_complexity": ["需要理解往返概念"],
                        "reasoning_steps": ["识别单程与往返的映射关系"],
                        "calculation_load": ["1400 × 2 的基本乘法"],
                        "reading_filter_load": ["从题干中提取距离数值和往返要求"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (job_dir / "difficulty_report.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "formula": "round(weighted_sum)",
                    "weighted_sum": 47.0,
                    "reading_difficulty": 47,
                    "warnings": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif skill_name == "reading_analysis/review_difficulty":
        raw_data = json.loads((job_dir / "difficulty_raw.json").read_text(encoding="utf-8"))
        (job_dir / "difficulty_reviewed.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "reading_difficulty": raw_data["reading_difficulty"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sha = hashlib.sha256((job_dir / "difficulty_raw.json").read_bytes()).hexdigest()
        (job_dir / "difficulty_review_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "question_id": "Q100",
                    "source_artifact": "difficulty_raw.json",
                    "source_artifact_sha256": sha,
                    "checks": [],
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif skill_name == "reading_analysis/generate_distractors":
        (job_dir / "distractors_raw.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "distractors": [
                        {
                            "id": "dist-1",
                            "source_text": "上海",
                            "normalized_text": "出发地名称",
                            "location": {
                                "source": "stem",
                                "option_key": None,
                                "start": 3,
                                "end": 5,
                            },
                            "relevance": "属于行程情境信息",
                            "non_necessity": "替换城市名称不影响距离计算",
                            "counterfactual": "替换为其他城市后解题过程和答案不变",
                            "confusion_strength": 72,
                            "confidence": 0.94,
                        },
                        {
                            "id": "dist-2",
                            "source_text": "北京",
                            "normalized_text": "目的地名称",
                            "location": {
                                "source": "stem",
                                "option_key": None,
                                "start": 8,
                                "end": 10,
                            },
                            "relevance": "属于行程情境信息",
                            "non_necessity": "替换城市名称不影响距离计算",
                            "counterfactual": "替换为其他城市后解题过程和答案不变",
                            "confusion_strength": 70,
                            "confidence": 0.92,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (job_dir / "distractors_report.json").write_text(
            json.dumps(
                {
                    "question_id": "Q100",
                    "candidate_count": 2,
                    "method": "semantic_extraction",
                    "keyword_conflicts_excluded": [],
                    "warnings": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif skill_name == "reading_analysis/review_distractors":
        raw_data = json.loads((job_dir / "distractors_raw.json").read_text(encoding="utf-8"))
        (job_dir / "distractors_reviewed.json").write_text(
            json.dumps(raw_data, ensure_ascii=False), encoding="utf-8"
        )
        sha = hashlib.sha256((job_dir / "distractors_raw.json").read_bytes()).hexdigest()
        (job_dir / "distractors_review_report.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "question_id": "Q100",
                    "source_artifact": "distractors_raw.json",
                    "source_artifact_sha256": sha,
                    "checks": [],
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def test_all_agent_nodes_have_complete_repository_skills():
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    root = Path("server/app/pipelines/skills")

    for node in definition.nodes.values():
        if node.agent is None:
            continue
        skill = resolve_pipeline_skill(root, node.agent.skill)
        assert (skill / "SKILL.md").is_file()
        assert (skill / "references" / "output-contract.md").is_file()
        assert (skill / "scripts" / "validate_output.py").is_file()


def test_resolve_pipeline_skill_rejects_escape(tmp_path):
    with pytest.raises(ValueError, match="skill path"):
        resolve_pipeline_skill(tmp_path, "../outside")


def test_resolve_pipeline_skill_rejects_absolute(tmp_path):
    with pytest.raises(ValueError, match="skill path"):
        resolve_pipeline_skill(tmp_path, "/outside")


def test_resolve_pipeline_skill_requires_contract_files(tmp_path):
    (tmp_path / "foo" / "SKILL.md").parent.mkdir(parents=True)
    (tmp_path / "foo" / "SKILL.md").write_text("x")
    with pytest.raises(ValueError, match="output-contract"):
        resolve_pipeline_skill(tmp_path, "foo")


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_accepts_valid_output_in_process(skill_name, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)
    _setup_skill_valid_outputs(skill_name, job_dir)

    errors = _run_validator(skill_name, job_dir)
    assert errors == []


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_rejects_missing_output_in_process(skill_name, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)

    try:
        errors = _run_validator(skill_name, job_dir)
    except FileNotFoundError as exc:
        # Review validators that call validate_review_result directly raise
        # FileNotFoundError when the report file is absent.
        assert any(name in str(exc) for name in ("review_report.json", "reviewed.json")), str(exc)
    else:
        assert errors
        assert any("Missing output file" in e for e in errors)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_rejects_malformed_json_in_process(skill_name, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)
    _setup_skill_valid_outputs(skill_name, job_dir)

    # Corrupt all JSON output files
    for path in job_dir.iterdir():
        if path.name.endswith(".json") and path.name != "questions_parsed.json":
            path.write_text("not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        _run_validator(skill_name, job_dir)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_rejects_schema_violation_in_process(skill_name, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)
    _setup_skill_valid_outputs(skill_name, job_dir)

    # Introduce a schema violation by removing a required field
    if skill_name == "reading_analysis/extract_keywords":
        (job_dir / "keywords_raw.json").write_text('{"question_id": "Q100"}', encoding="utf-8")
    elif skill_name == "reading_analysis/review_keywords":
        (job_dir / "keywords_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )
    elif skill_name == "reading_analysis/assess_difficulty":
        (job_dir / "difficulty_raw.json").write_text('{"question_id": "Q100"}', encoding="utf-8")
    elif skill_name == "reading_analysis/review_difficulty":
        (job_dir / "difficulty_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )
    elif skill_name == "reading_analysis/generate_distractors":
        (job_dir / "distractors_raw.json").write_text('{"question_id": "Q100"}', encoding="utf-8")
    elif skill_name == "reading_analysis/review_distractors":
        (job_dir / "distractors_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )

    errors = _run_validator(skill_name, job_dir)
    assert errors


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_rejects_invalid_report_in_process(skill_name, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)
    _setup_skill_valid_outputs(skill_name, job_dir)

    # Corrupt only the report file
    if skill_name == "reading_analysis/extract_keywords":
        (job_dir / "keywords_report.json").write_text('{"question_id": "Q100"}', encoding="utf-8")
    elif skill_name == "reading_analysis/review_keywords":
        (job_dir / "keywords_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )
    elif skill_name == "reading_analysis/assess_difficulty":
        (job_dir / "difficulty_report.json").write_text('{"question_id": "Q100"}', encoding="utf-8")
    elif skill_name == "reading_analysis/review_difficulty":
        (job_dir / "difficulty_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )
    elif skill_name == "reading_analysis/generate_distractors":
        (job_dir / "distractors_report.json").write_text(
            '{"question_id": "Q100"}', encoding="utf-8"
        )
    elif skill_name == "reading_analysis/review_distractors":
        (job_dir / "distractors_review_report.json").write_text(
            '{"status": "passed", "question_id": "Q100"}', encoding="utf-8"
        )

    errors = _run_validator(skill_name, job_dir)
    assert errors


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_validator_cli_smoke_accepts_valid_output(skill_name, tmp_path):
    """Keep one subprocess smoke per skill to verify the CLI entry point."""
    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    _setup_skill_inputs(skill_name, job_dir)
    _setup_skill_valid_outputs(skill_name, job_dir)

    result = subprocess.run(
        [sys.executable, str(validator), str(job_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_validator_usage_requires_directory_argument():
    validator = Path(
        "server/app/pipelines/skills/reading_analysis/extract_keywords/scripts/validate_output.py"
    )
    result = subprocess.run(
        [sys.executable, str(validator)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Usage" in result.stderr


def test_all_skill_validators_are_executable():
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    root = Path("server/app/pipelines/skills")

    for node in definition.nodes.values():
        if node.agent is None:
            continue
        skill = resolve_pipeline_skill(root, node.agent.skill)
        validator = skill / "scripts" / "validate_output.py"
        assert validator.stat().st_mode & 0o111, f"{validator} is not executable"


EXPECTED_SKILLS = {
    "extract_keywords",
    "review_keywords",
    "assess_difficulty",
    "review_difficulty",
    "generate_distractors",
    "review_distractors",
}


def test_reading_analysis_has_six_complete_skills():
    definition = load_pipeline_definition(Path("config/pipelines/reading_analysis.yaml"))
    actual = {node.key for node in definition.nodes.values() if node.agent is not None}
    assert actual == EXPECTED_SKILLS
