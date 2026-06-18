# Output Contract for Generate Distractors

## distractors_raw.json

A flat JSON object (not a `questions` array wrapper) with:

- `question_id`: string matching the source question ID.
- `distractors`: array of distractor objects. Each distractor must contain:
  - `id`: unique string identifier.
  - `source_text`: exact text slice from the question.
  - `normalized_text`: semantic label of the distractor.
  - `location`: object with `source` (`stem` or `option`), `option_key` (null or string), `start`, and `end` offsets.
  - `relevance`: non-empty string explaining why the text is related to the scenario.
  - `non_necessity`: non-empty string explaining why it is not required.
  - `counterfactual`: non-empty string describing the replacement or deletion test.
  - `confusion_strength`: integer in [1, 99].
  - `confidence`: number in [0, 1].

Distractors must not overlap with keywords by `source_text` or by location range.

## distractors_report.json

A flat JSON object with:

- `question_id`: string matching the source question ID.
- `candidate_count`: integer count of distractors in the raw artifact.
- `method`: string describing the generation method.
- `keyword_conflicts_excluded`: list of keyword source_text strings that were excluded due to overlap.
- `warnings`: array of warning strings (may be empty).
