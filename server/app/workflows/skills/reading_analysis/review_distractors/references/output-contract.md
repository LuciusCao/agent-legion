# Output Contract for Review Distractors

## distractors_review_report.json

A flat JSON object (not a `questions` array wrapper) with:

- `status`: exactly `"passed"` or `"failed"`.
- `question_id`: string matching the source question ID.
- `source_artifact`: string naming the raw artifact, e.g. `"distractors_raw.json"`.
- `source_artifact_sha256`: hex SHA-256 digest of `distractors_raw.json`.
- `checks`: array of check objects. Each check contains:
  - `code`: string check code (e.g. `"LOCATION"`, `"RELEVANCE"`, `"NON_NECESSITY"`, `"KEYWORD_CONFLICT"`).
  - `item_id`: string distractor `id`.
  - `passed`: boolean.
- `issues`: array of issue objects. Each issue contains:
  - `code`: string issue code.
  - `item_id`: string distractor `id`.
  - `message`: human-readable description.
  - `evidence`: supporting evidence string.

Rules:
- `failed` status requires at least one issue and must NOT have a `distractors_reviewed.json` file.
- `passed` status requires zero issues and MUST have a `distractors_reviewed.json` file.

## distractors_reviewed.json

A flat JSON object that is an **exact semantic copy** of `distractors_raw.json`. It is only produced when the review status is `"passed"`.
