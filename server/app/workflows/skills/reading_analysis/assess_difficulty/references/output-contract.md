# Output Contract for Assess Difficulty

## Artifacts

- `difficulty_raw.json` — flat JSON object containing dimension scores, weights, evidence, and computed difficulty for a single question.
- `difficulty_report.json` — flat JSON object containing generation metadata for a single question.

## difficulty_raw.json

```json
{
  "question_id": "Q100",
  "dimensions": {
    "knowledge_complexity": 42,
    "reasoning_steps": 55,
    "calculation_load": 28,
    "reading_filter_load": 61
  },
  "weights": {
    "knowledge_complexity": 0.3,
    "reasoning_steps": 0.3,
    "calculation_load": 0.2,
    "reading_filter_load": 0.2
  },
  "reading_difficulty": 47,
  "evidence": {
    "knowledge_complexity": ["使用一次乘法模型"],
    "reasoning_steps": ["识别往返后乘以 2"],
    "calculation_load": ["1400 × 2"],
    "reading_filter_load": ["需要区分城市名称与距离条件"]
  }
}
```

### Field descriptions

- `question_id` — string, must match the source question.
- `dimensions` — object with exactly four keys:
  - `knowledge_complexity`: integer in [1, 99]
  - `reasoning_steps`: integer in [1, 99]
  - `calculation_load`: integer in [1, 99]
  - `reading_filter_load`: integer in [1, 99]
- `weights` — object with exactly the same four keys as `dimensions`. Each value is a float in [0, 1], and the sum of all four weights must equal 1.0 within 1e-9.
- `reading_difficulty` — integer in [1, 99], must equal `round(sum(dimension * weight))`.
- `evidence` — object with exactly the same four keys as `dimensions`. Each value is a non-empty array of non-empty strings explaining the rationale for that score.

## difficulty_report.json

```json
{
  "question_id": "Q100",
  "formula": "round(weighted_sum)",
  "weighted_sum": 46.9,
  "reading_difficulty": 47,
  "warnings": []
}
```

### Field descriptions

- `question_id` — string, must match the source question.
- `formula` — string describing the aggregation formula.
- `weighted_sum` — number, the exact weighted sum before rounding.
- `reading_difficulty` — integer in [1, 99], must match `reading_difficulty` in `difficulty_raw.json`.
- `warnings` — array of warning strings (may be empty).
