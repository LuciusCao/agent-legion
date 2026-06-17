# Output Contract For Generate Possible Errors

`possible_errors_raw.json` contains:

- `question_id`: string matching `questions_parsed.json`.
- `possible_error_list`: array of error objects.

Each error object contains:

- `error_id`: string beginning with `pe_`.
- `error_type`: always `question_comprehension`.
- `error_answer`: non-empty string representing the wrong answer a student might give.
- `error_description`: non-empty string explaining the comprehension mistake.
- `related_key_info_ids`: array of `key_info_id` strings from `key_info_reviewed.json`; may be empty.

`possible_errors_report.json` contains:

- `question_id`
- `warnings`: array of strings
