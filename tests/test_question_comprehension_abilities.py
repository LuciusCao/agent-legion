import json
from pathlib import Path

TAXONOMY = (
    Path(__file__).resolve().parents[1]
    / "server/app/pipelines/skills/question_comprehension_info/_shared/references/question_comprehension_abilities.json"
)


def test_question_comprehension_ability_taxonomy_contains_expected_ids():
    data = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    ability_ids = {ability["id"] for ability in data["abilities"]}
    sub_ids = {sub["id"] for ability in data["abilities"] for sub in ability["sub_abilities"]}

    assert data["version"] == "1.0"
    assert ability_ids == {
        "task_understanding",
        "information_extraction",
        "language_comprehension",
        "relational_reasoning",
    }
    assert sub_ids == {
        "goal_identification",
        "answer_type_recognition",
        "instruction_recognition",
        "information_locating",
        "information_filtering",
        "inference",
        "keyword_comprehension",
        "complex_sentence_parsing",
        "reference_resolution",
        "relationship_identification",
        "constraint_identification",
        "condition_sequencing",
    }
