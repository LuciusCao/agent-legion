# Generate Possible Errors

Generate possible question-comprehension errors from the reviewed key info and parsed question.

## Inputs
- `questions_parsed.json`
- `key_info_reviewed.json`

## Outputs
- `possible_errors_raw.json`
- `possible_errors_report.json`

## Workflow
1. Read `references/output-contract.md`.
2. Read all declared inputs.
3. For each plausible comprehension mistake, produce an error item with a `pe_` ID.
4. Link each error to relevant `key_info_id`s from `key_info_reviewed.json`.
5. Write outputs.
6. Run `python scripts/validate_output.py .`.
7. Fix artifacts and rerun validation until it succeeds.

Do not compute or modify `fingerprint`. Do not create files outside the output list.
