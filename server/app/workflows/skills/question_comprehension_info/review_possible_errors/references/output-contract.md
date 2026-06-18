# Output Contract For Review Possible Errors

`possible_errors_reviewed.json` follows the same schema as `possible_errors_raw.json`.

`possible_errors_reviewed.json` contains:

- `question_id`: string matching `questions_parsed.json`.
- `possible_error_list`: array of error objects.

Each error object contains:

- `error_id`: string beginning with `pe_`.
- `error_type`: always `question_comprehension`.
- `error_answer`: non-empty string representing the wrong answer a student might give.
- `error_description`: non-empty string explaining the comprehension mistake.
- `related_key_info_ids`: array of `key_info_id` strings from `key_info_reviewed.json`; may be empty.

`possible_errors_review_report.json` contains:

- `question_id`
- `approved_count`
- `rejected_count`
- `warnings`: array of strings
- `decisions`: array with `error_id`, `decision` (`approved` or `rejected`), and `reason`.
