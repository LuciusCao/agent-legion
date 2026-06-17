# Output Contract For Assess Comprehension Difficulty

`comprehension_difficulty.json` must contain:
- `question_id`: string matching `questions_parsed.json`.
- `comprehension_difficulty`: integer in the range 1..99 inclusive.
- `signals`: object with non-negative integer counts:
  - `key_info_count`
  - `hidden_info_count`
  - `possible_error_count`
  - `ability_count`
- `evidence`: non-empty array of strings explaining the score.

`comprehension_difficulty_report.json` contains:
- `question_id`
- `warnings`: array of strings
- `method`: string describing how the score was derived
