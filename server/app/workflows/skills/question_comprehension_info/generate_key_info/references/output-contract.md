# Output Contract For Generate Key Info

`key_info_raw.json` is a flat JSON object with:

- `question_id`: string matching `questions_parsed.json`.
- `key_info_list`: array of key info objects.

Each key info object contains:

- `key_info_id`: string beginning with `ki_`.
- `type`: `given` or `hidden`.
- `content`: for `given`, `{ "text": string, "position": { "start": int, "end": int } }`; for `hidden`, `{ "derived_text": string, "position": { "start": int, "end": int }, "derivation": string }`.
  - `position.start`/`end` are zero-based indices into the **plain text** of the question stem (HTML tags stripped).
  - For `given` items, `content.text` must exactly match `plain_stem[start:end]`.
- `question`: Socratic check question with non-empty `text` and `options`.
- `question.options[]`: each option has `label`, `text`, and `is_correct`; at least one option must be correct.
- `question_comprehension_abilities`: non-empty array of second-level ability IDs from the shared taxonomy.

`key_info_report.json` contains:

- `question_id`
- `warnings`: array of strings
