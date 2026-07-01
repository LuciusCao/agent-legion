# Comprehension Uploader

A standalone CLI tool for uploading comprehension info batches. It is decoupled
from `server.app.*` and uses only project dependencies (`requests`, `pydantic`,
`pyyaml`).

## Installation

The tool lives under `tools/comprehension-uploader` and is invoked through the
project's `uv` environment. No extra dependencies are required.

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit it for your environment:

```yaml
api_base_url: "http://dev-basic-be-addonsquestionsource.be.dev.example.com"
auth_token_env: "COMPREHENSION_API_TOKEN"
db_path: "data/comprehension_uploader.db"
question_source:
  type: "json_file"
  path: "data/question_snapshots.json"
upload_on_duplicate: "update"
request_timeout: 30
max_retries: 3
```

The bearer token is read from the environment variable named by
`auth_token_env`.

## Input format (`package.jsonl`)

Each line is a JSON object:

```json
{
  "question_id": "Q100",
  "subject_id": 2,
  "question_uuid": "65ff689afdebf4b7ca7bf71113ee5d51",
  "question_vno": 1745999400,
  "comprehension_difficulty": 50,
  "format_vno": "v1",
  "comprehension_data": { "steps": [...] },
  "stem": "题干文本",
  "options": [{"label": "A", "text": "选项 A"}]
}
```

- `comprehension_data` may be a dict or list; it is JSON-stringified before
  calling the API.
- The fingerprint is computed from `stem` + `options`. If those fields are
  missing but a `fingerprint` field is provided, the provided fingerprint is
  trusted with a warning.

## Commands

Initialize the SQLite database:

```bash
uv run python tools/comprehension-uploader/run.py init-db --config config.yaml
```

Upload a batch:

```bash
uv run python tools/comprehension-uploader/run.py upload \
  --config config.yaml \
  --workspace ws-123 \
  package.jsonl
```

`--workspace` is the recommended identifier. It is stored as `workspace_id` in
the SQLite logs, and the `batch_id` defaults to `<workspace>-<timestamp>` so
each run has a unique batch id. If you need a fixed batch id, pass
`--batch-id` explicitly; it overrides the generated one.

### Makefile shortcuts

`make` does not parse `--flags` directly, so pass arguments via variables or
`ARGS`:

```bash
# Variable style (recommended)
make upload WORKSPACE=ws-123 CONFIG=config.prod.yaml PACKAGE=package.jsonl

# ARGS style (passes raw flags to the CLI)
make upload ARGS="--workspace ws-123 --config config.prod.yaml package.jsonl"

# Scan for stale questions
make scan-comprehension CONFIG=config.prod.yaml OUTPUT=stale.json
```

Scan for questions whose fingerprint has changed and need re-generation:

```bash
uv run python tools/comprehension-uploader/run.py scan \
  --config config.yaml \
  --output stale.json
```

Show the current state and upload history for a question:

```bash
uv run python tools/comprehension-uploader/run.py status --config config.yaml Q100
```

You can also run the package module directly when the tool directory is on
`PYTHONPATH`:

```bash
PYTHONPATH=tools/comprehension-uploader \
  uv run python -m comprehension_uploader upload --config config.yaml --batch-id 20260701-v1 package.jsonl
```

## Duplicate handling

When the API returns `code=11051` for an existing question, the tool branches
based on `upload_on_duplicate`:

- `update` – call `/v1/updateComprehensionInfo` with only the fields that
  changed or are non-empty (`comprehension_difficulty`, `comprehension_data`,
  `format_vno`).
- `skip` – record the attempt as skipped and leave the remote data unchanged.

## Tests

Tests are located at `tests/tools/test_comprehension_uploader.py` and can be
run with:

```bash
uv run pytest tests/tools/test_comprehension_uploader.py -q
```
