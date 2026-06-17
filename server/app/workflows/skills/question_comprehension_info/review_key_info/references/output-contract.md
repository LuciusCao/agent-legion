# Output Contract For Review Key Info

`key_info_reviewed.json` follows the same schema as `key_info_raw.json`.

`key_info_review_report.json` contains:

- `question_id`
- `approved_count`
- `rejected_count`
- `warnings`
- `decisions`: array with `key_info_id`, `decision` (`approved` or `rejected`), and `reason`.
