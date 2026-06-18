# Output Contract for Review Difficulty

## Artifacts

- `difficulty_reviewed.json` — flat JSON object projected for CMS consumption.
- `difficulty_review_report.json` — flat JSON object containing the review result.

## difficulty_reviewed.json

```json
{
  "question_id": "Q100",
  "reading_difficulty": 47
}
```

### Field descriptions

- `question_id` — string, must match the source question.
- `reading_difficulty` — integer in [1, 99], must match `reading_difficulty` in `difficulty_raw.json`.

No other fields are permitted.

## difficulty_review_report.json

```json
{
  "status": "passed",
  "question_id": "Q100",
  "source_artifact": "difficulty_raw.json",
  "source_artifact_sha256": "...",
  "checks": [],
  "issues": []
}
```

### Field descriptions

- `status` — string, exactly `"passed"` or `"failed"`.
- `question_id` — string, must match the source question.
- `source_artifact` — string, name of the source artifact (`difficulty_raw.json`).
- `source_artifact_sha256` — string, SHA-256 hex digest of the source artifact file.
- `checks` — array of check entries (may be empty).
- `issues` — array of issue entries (must be non-empty when `status` is `"failed"`, must be empty when `status` is `"passed"`).

### Rules

- A failed review must not produce `difficulty_reviewed.json`.
- A passed review must produce `difficulty_reviewed.json` and contain no issues.
