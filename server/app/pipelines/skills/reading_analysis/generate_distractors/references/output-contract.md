# Output Contract for Generate Distractors

## distractors_raw.json

A JSON object with a `questions` array. Each element must contain at minimum:

- `question_id`: string matching the source question ID.

Additional fields are specific to the node purpose.

## distractors_report.json

A JSON object with a `questions` array and a `summary` object. The summary must contain:

- `total`: integer count of processed questions.
- `warnings`: array of warning strings (may be empty).

Additional summary fields are specific to the node purpose.
