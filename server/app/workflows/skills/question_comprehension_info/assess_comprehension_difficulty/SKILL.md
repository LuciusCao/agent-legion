# Assess Comprehension Difficulty

Assess only the difficulty of question comprehension, not solution difficulty.

## Inputs
- `questions_parsed.json`
- `key_info_reviewed.json`
- `possible_errors_reviewed.json`

## Outputs
- `comprehension_difficulty.json`
- `comprehension_difficulty_report.json`

## Workflow
1. Read `../_shared/references/question_comprehension_abilities.json`.
2. Read `references/output-contract.md`.
3. Read all declared inputs.
4. Score `comprehension_difficulty` from 1 to 99 based on task ambiguity, key/hidden information, language complexity, relation construction, and likely comprehension errors.
5. Do not score solution knowledge or calculation complexity except when it affects comprehension.
6. Write outputs.
7. Run `python scripts/validate_output.py .`.
8. Fix artifacts and rerun validation until it succeeds.

Do not compute or modify `fingerprint`.
