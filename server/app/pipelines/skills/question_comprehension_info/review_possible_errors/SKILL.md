# Review Possible Errors

Review generated possible errors and produce the reviewed artifact with a report.

## Inputs
- `questions_parsed.json`
- `key_info_reviewed.json`
- `possible_errors_raw.json`

## Outputs
- `possible_errors_reviewed.json`
- `possible_errors_review_report.json`

## Workflow
1. Read `references/output-contract.md`.
2. Read all declared inputs.
3. Validate whether each possible error is plausible, well described, and linked to valid key info.
4. Preserve valid item IDs. Remove invalid items only when the report explains why.
5. Write outputs.
6. Run `python scripts/validate_output.py .`.
7. Fix artifacts and rerun validation until it succeeds.

Do not compute or modify `fingerprint`. Do not create files outside the output list.
