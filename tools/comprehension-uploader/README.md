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
db_path: "data/comprehension_uploader.db"
question_source:
  type: "json_file"
  path: "data/question_snapshots.json"
upload_on_duplicate: "update"
request_timeout: 30
max_retries: 3
```

## Authentication

The tool follows the same CMS token flow used by the rest of the project. It
automatically loads `.env` if present.

Priority:

1. `BASECMS_TOKEN` environment variable (or `.env` entry).
2. Generated token from `BASECMS_APP_ID`, `BASECMS_NONCE`, `BASECMS_SECRET`,
   `BASECMS_TOKEN_URL`. The generation is the same HMAC-SHA256 flow as
   `server/app/cms/auth.py`.
3. Optional `token` / `token_gen` overrides in `config.yaml`.

Typical usage with the project's `.env`:

```bash
# 1. Build package.jsonl from agent-legion comprehension_info.json artifacts
make package-comprehension \
  INPUT_DIR=./workspace-output \
  CONFIG=config.prod.yaml \
  OUTPUT=package.jsonl

# 2. Upload the batch
make upload WORKSPACE=ws-123 CONFIG=config.prod.yaml PACKAGE=package.jsonl

# 3. (Later) scan for questions whose content changed and need re-generation
make scan-comprehension CONFIG=config.prod.yaml OUTPUT=stale.json
```

No extra `export` is needed as long as `BASECMS_APP_ID`, `BASECMS_NONCE`,
`BASECMS_SECRET`, and `BASECMS_TOKEN_URL` are set in `.env`.

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
  "comprehension_data": {
    "fingerprint": "...",
    "comprehension_difficulty": 50,
    "key_info_list": [...],
    "possible_error_list": [...]
  },
  "stem": "题干文本",
  "options": [{"label": "A", "text": "选项 A"}]
}
```

- `comprehension_data` is JSON-stringified before calling the API.
- `format_vno` is the comprehension info schema version. It is resolved in this
  order:
  1. The `format_vno` field on the input line.
  2. The `comprehension_info_schema_version` field on the input line.
  3. Defaults to `"v1"` with a warning.
- The resolved `format_vno` is written back to the upload record and sent to the
  API, so the API payload, update fields and upload logs always agree.
- The fingerprint is computed from `stem` + `options`. If those fields are
  missing but a `fingerprint` field is provided, the provided fingerprint is
  trusted with a warning.

## Schema versioning

`comprehension_info.json` declares its structure version in the top-level
`schema_version` field. The version maps directly to the API's `format_vno`:

```json
{
  "question_id": "Q100",
  "schema_version": "v1",
  "comprehension_data": { ... }
}
```

The `comprehension_data` field itself does **not** contain `schema_version`;
that stays at the top level. Supported versions are defined under
`tools/comprehension-uploader/comprehension_uploader/schemas/` and validated before upload.

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

Build a `package.jsonl` from `comprehension_info.json` files:

```bash
uv run python tools/comprehension-uploader/run.py package \
  --config config.yaml \
  --input-dir ./workspace-output \
  --output package.jsonl
```

The `package` command walks `--input-dir`, reads each `comprehension_info.json`,
takes the top-level `schema_version` as `format_vno` (defaulting to `"v1"`), and
looks up the latest question content from the configured `question_source` to
fill `question_id`, `subject_id`, `question_uuid`, `question_vno`, `stem` and
`options`.

Validate a package without uploading:

```bash
uv run python tools/comprehension-uploader/run.py validate package.jsonl
```

This parses every line and runs the declared schema version validator,
printing a pass/fail summary.

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

### Makefile shortcuts

`make` does not parse `--flags` directly, so pass arguments via variables or
`ARGS`:

```bash
# Variable style (recommended)
make upload WORKSPACE=ws-123 CONFIG=config.prod.yaml PACKAGE=package.jsonl

# ARGS style (passes raw flags to the CLI)
make upload ARGS="--workspace ws-123 --config config.prod.yaml package.jsonl"

# Build a package.jsonl from comprehension_info.json files
make package-comprehension INPUT_DIR=./workspace-output CONFIG=config.prod.yaml OUTPUT=package.jsonl

# Scan for stale questions
make scan-comprehension CONFIG=config.prod.yaml OUTPUT=stale.json
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
