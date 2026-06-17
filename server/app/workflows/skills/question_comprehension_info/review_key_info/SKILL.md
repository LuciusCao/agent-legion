# Review Key Info

Review `key_info_raw.json` against the parsed question and shared ability taxonomy. Produce the reviewed artifact and a review report.

## Inputs
- `questions_parsed.json`
- `key_info_raw.json`

## Outputs
- `key_info_reviewed.json`
- `key_info_review_report.json`

## Workflow
1. Read `../_shared/references/question_comprehension_abilities.json`.
2. Read `references/output-contract.md`.
3. Read every declared input artifact.
4. Validate whether each key info item is necessary, well located, and tagged with appropriate second-level abilities.
5. Preserve valid item IDs. Remove invalid items only when the report explains why.
6. Write `key_info_reviewed.json` and `key_info_review_report.json`.
7. Run `python scripts/validate_output.py .`.
8. Fix artifacts and rerun validation until it succeeds.

Do not compute or modify `fingerprint`. Do not create files outside the output list.
