# Generate Distractors

Read only the input artifacts named in this skill. Produce the required output artifact in the current job directory.

## Inputs
- `questions_parsed.json`
- `keywords_reviewed.json`

## Output
- `distractors_raw.json`
- `distractors_report.json`

## Core Rule

A reading distractor is different from an answer distractor. A candidate is related to the scenario or topic, but replacing or deleting it leaves the model, required constraints, solution path, and answer unchanged.

This skill controls the final candidate count directly. Do not intentionally over-generate for a later selector, because every approved candidate enters CMS.

## Workflow
1. Read `references/output-contract.md`.
2. Read every declared input artifact from the current directory.
3. Analyze the question according to the reference contract.
4. Write `distractors_raw.json` and `distractors_report.json` directly in the current directory.
5. Run `python scripts/validate_output.py .` using the absolute script path supplied in the prompt.
6. Fix the artifact and rerun validation until it exits successfully.

Do not create files outside the declared output list. Do not modify input artifacts.
