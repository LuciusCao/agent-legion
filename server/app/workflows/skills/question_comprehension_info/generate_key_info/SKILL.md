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
6. Write `key_info_raw.json` and `key_info_report.json`.
7. Run `python scripts/validate_output.py .`.
8. Fix artifacts and rerun validation until it succeeds.

Do not compute or modify `fingerprint`. Do not create files outside the output list.
