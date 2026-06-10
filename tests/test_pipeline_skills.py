from pathlib import Path

import pytest

from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.skills import resolve_pipeline_skill


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


@pytest.mark.parametrize(
    "skill_name",
    [
        "reading_analysis/extract_keywords",
        "reading_analysis/review_keywords",
        "reading_analysis/assess_difficulty",
        "reading_analysis/review_difficulty",
        "reading_analysis/generate_distractors",
        "reading_analysis/review_distractors",
    ],
)
def test_validator_accepts_valid_output(skill_name, tmp_path):
    import subprocess

    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"

    script = validator.read_text()
    import ast

    tree = ast.parse(script)
    outputs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            elts = [ast.literal_eval(e) for e in node.elts if isinstance(e, ast.Constant)]
            if all(e.endswith(".json") for e in elts):
                outputs = elts
                break

    for out in outputs:
        if out.endswith("_report.json"):
            (tmp_path / out).write_text(
                '{"questions": [], "summary": {"total": 0, "warnings": []}}'
            )
        else:
            (tmp_path / out).write_text('{"questions": []}')

    result = subprocess.run(
        ["python", str(validator), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "skill_name",
    [
        "reading_analysis/extract_keywords",
        "reading_analysis/review_keywords",
        "reading_analysis/assess_difficulty",
        "reading_analysis/review_difficulty",
        "reading_analysis/generate_distractors",
        "reading_analysis/review_distractors",
    ],
)
def test_validator_rejects_missing_output(skill_name, tmp_path):
    import subprocess

    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"

    result = subprocess.run(
        ["python", str(validator), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Missing" in result.stderr


@pytest.mark.parametrize(
    "skill_name",
    [
        "reading_analysis/extract_keywords",
        "reading_analysis/review_keywords",
        "reading_analysis/assess_difficulty",
        "reading_analysis/review_difficulty",
        "reading_analysis/generate_distractors",
        "reading_analysis/review_distractors",
    ],
)
def test_validator_rejects_malformed_json(skill_name, tmp_path):
    import subprocess

    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"

    script = validator.read_text()
    import ast

    tree = ast.parse(script)
    outputs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            elts = [ast.literal_eval(e) for e in node.elts if isinstance(e, ast.Constant)]
            if all(e.endswith(".json") for e in elts):
                outputs = elts
                break

    for out in outputs:
        (tmp_path / out).write_text("not json")

    result = subprocess.run(
        ["python", str(validator), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Invalid JSON" in result.stderr


@pytest.mark.parametrize(
    "skill_name",
    [
        "reading_analysis/extract_keywords",
        "reading_analysis/review_keywords",
        "reading_analysis/assess_difficulty",
        "reading_analysis/review_difficulty",
        "reading_analysis/generate_distractors",
        "reading_analysis/review_distractors",
    ],
)
def test_validator_rejects_missing_questions_key(skill_name, tmp_path):
    import subprocess

    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"

    script = validator.read_text()
    import ast

    tree = ast.parse(script)
    outputs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            elts = [ast.literal_eval(e) for e in node.elts if isinstance(e, ast.Constant)]
            if all(e.endswith(".json") for e in elts):
                outputs = elts
                break

    for out in outputs:
        (tmp_path / out).write_text('{"other": true}')

    result = subprocess.run(
        ["python", str(validator), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "questions" in result.stderr


@pytest.mark.parametrize(
    "skill_name",
    [
        "reading_analysis/extract_keywords",
        "reading_analysis/review_keywords",
        "reading_analysis/assess_difficulty",
        "reading_analysis/review_difficulty",
        "reading_analysis/generate_distractors",
        "reading_analysis/review_distractors",
    ],
)
def test_validator_rejects_missing_summary_in_report(skill_name, tmp_path):
    import subprocess

    root = Path("server/app/pipelines/skills")
    skill = resolve_pipeline_skill(root, skill_name)
    validator = skill / "scripts" / "validate_output.py"

    script = validator.read_text()
    import ast

    tree = ast.parse(script)
    outputs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            elts = [ast.literal_eval(e) for e in node.elts if isinstance(e, ast.Constant)]
            if all(e.endswith(".json") for e in elts):
                outputs = elts
                break

    for out in outputs:
        if out.endswith("_report.json"):
            (tmp_path / out).write_text('{"questions": []}')
        else:
            (tmp_path / out).write_text('{"questions": []}')

    result = subprocess.run(
        ["python", str(validator), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "summary" in result.stderr


def test_validator_usage_requires_directory_argument():
    import subprocess

    validator = Path(
        "server/app/pipelines/skills/reading_analysis/extract_keywords/scripts/validate_output.py"
    )
    result = subprocess.run(
        ["python", str(validator)],
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
