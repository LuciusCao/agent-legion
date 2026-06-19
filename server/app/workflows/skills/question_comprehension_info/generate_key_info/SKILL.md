# Generate Key Info

Read only the declared inputs and produce only the declared outputs in the current job directory.

## Inputs
- `questions_parsed.json`

## Outputs
- `key_info_raw.json`
- `key_info_report.json`

## Workflow
1. Read `../_shared/references/question_comprehension_abilities.json`.
2. Read `references/output-contract.md`.
3. Read `questions_parsed.json`.
4. Generate key information needed for question comprehension.
5. Each key info item must use only second-level ability IDs from the taxonomy.
6. `content.position.start`/`end` must be zero-based indices into the **plain text** of the question stem (HTML tags removed).
7. Write a draft of `key_info_raw.json` and `key_info_report.json`.
8. Run `python scripts/normalize_positions.py .` to convert any HTML-based positions into plain-text positions.
9. Run `python scripts/validate_output.py .`.
10. Fix artifacts and rerun steps 8-9 until validation succeeds.

Do not compute or modify `fingerprint`. Do not create files outside the output list.
