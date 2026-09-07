# Campaign Submission Runbook

Operator guide for large batch campaigns — submitting hundreds of thousands of
items into one long-lived workspace without tripping the platform's capacity
guardrails. The tool is `scripts/submit_campaign.py` (#505): a drip-feed
submitter that replaces the hand-rolled "read the water level → POST a batch →
retry on failure" loop every operator used to write themselves.

> Capacity framing comes from #349 (capacity evaluation and run baseline):
> the non-terminal job count red line is ~5×10^4 per instance, and the
> submission-side mitigations ("batched submission, 5k–2×10^4 items per run")
> are exactly what this tool automates. Campaign-time fleet and DB tuning
> (enqueue workers, pool size, heartbeat intervals, retention windows) also
> live in #349's baseline table — this runbook only covers the submission
> channel.

## 1. What it does

Given a workspace with a published workflow revision and a manifest file of
items, the tool loops:

1. Read the workspace's non-terminal job count (water level).
2. If the level is below the watermark, POST the next batch of items as
   `POST /workspaces/{id}/runs` (batch size ≤ `workflows.max_items_per_run`).
3. Otherwise sleep `--poll-interval` seconds and re-check.
4. Stop when the manifest is fully submitted.

Each batch becomes one run (one job per item, deduplicated server-side), so
the campaign shows up as a sequence of runs in the workspace run list.

## 2. Usage

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/submit_campaign.py \
    --base http://127.0.0.1:8000 \
    --username admin --password '***' \
    --workspace-id <workspace-id> \
    --items campaign.jsonl
```

Key parameters:

| Flag | Default | Meaning |
|------|---------|---------|
| `--items` | (required) | Manifest: `.jsonl` (one item per line, order preserved) or `.csv` (rows become items). Item contract is exactly `POST /runs`: `{"type": "material", "material_id": ...}`, `{"type": "bundle", "bundle_id": ...}`, `{"type": "ref", "connection_key": ..., "external_id": ..., "params": {...}}`. Note: CSV cells are plain strings — `params` (a JSON object) is only expressible in `.jsonl`; CSV manifests are limited to string-only fields. |
| `--watermark` | `30000` | Water line: max non-terminal jobs in the workspace before the tool waits instead of submitting. |
| `--batch-size` | `5000` | Items per POST. Must be ≤ `workflows.max_items_per_run` (default 2×10^4; the tool reads the live instance setting at startup and refuses a larger batch). |
| `--poll-interval` | `10` | Seconds between water-level polls while the level is at/above the watermark. |
| `--retry-wait` / `--retry-max` | `5` / `0` | Linear backoff between batch retries (single wait capped at 60s); `0` retries forever (safe — see idempotency below). Only 5xx and network errors are retried — deterministic 4xx (401/403/422) fail immediately with a fix hint. |
| `--password` | (env) | Login password; when omitted, read from `AGENT_LEGION_CAMPAIGN_PASSWORD` (keeps the secret out of shell history and `ps`). |
| `--dry-run` | off | Print the batch plan (batch boundaries, current level) without logging in or submitting anything. |

The operator account needs the **admin role**: the batch-size guard reads
`GET /api/admin/instance-settings` (`require_admin`); a non-admin session
exits with 403 before any batch is submitted. Admin is only needed for that
read — the submission endpoints themselves are ordinary workspace APIs.

Example manifest (`campaign.jsonl`):

```jsonl
{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1001"}
{"type": "ref", "connection_key": "cms-main", "external_id": "Q-1002"}
{"type": "material", "material_id": "mat-abc"}
```

Exit codes: `0` done (including dry-run); `2` usage error (bad manifest,
auth failure, invalid flags — fixable by the operator); `3` submission error
(retries exhausted or unrecoverable 4xx).

## 3. Water-level semantics and the default watermark

**What counts as "non-terminal".** The tool reads
`GET /api/workspaces/{id}/stats` → `job_stats` (a trigger-maintained counter
table, PK-lookup cost regardless of workspace size) and counts
`total − completed − failed`. This deliberately includes `paused` and
`awaiting_approval` jobs: #349's red line is about the scheduler/rescan load
of the non-terminal *set*, and paused/approval-pending jobs stay in that set
(a paused backlog is not spare water capacity). Unknown statuses also count,
conservatively.

**Default watermark 3×10^4** (issue #505's on-machine calibration): steady-state
scheduler pass sits around ~1s, leaving an order of magnitude of headroom to
the 15s slow-pass warning (the pass-loop profiler flags any pass over 15s);
it also stays under #349's 5×10^4 operational red line with buffer for
provider slowdowns. Tune down when the fleet is slower than baseline; the
guardrails refuse a watermark smaller than the batch size (the loop could
never admit a batch).

**Batch size default 5k** matches the #467 chunked-submission baseline
(5k items measured at 6.9s bounded return). Larger batches (up to 2×10^4)
are fine when the submission cadence, not the fleet drain rate, is the
bottleneck.

## 4. Idempotency (the property that makes retries safe)

**Crash / kill -9 / restart and rerun the same command: zero duplicate jobs,
zero lost items.** This is a property of the server-side submission path,
not local state — the tool keeps no submission journal. The chain:

- **Cross-batch dedup**: `RunService.create_run`
  (`server/app/services/run_service.py`) filters items whose
  `(source_type, source_id)` already has a job in the workspace
  (`JobQueries.filter_existing_dedup_keys`, chunked point probes). A
  resubmitted batch skips every already-created job and only creates the
  missing ones.
- **Mid-batch failure self-healing**: job insertion commits in bounded chunks
  (#467 A3, `create_jobs_bulk`). If a batch dies partway, committed chunks
  stay and the run row is marked failed with its progress
  (`run_partial_failure.compensate_partial_creation`); resubmitting the same
  items re-enters through the dedup filter above, and the deterministic run id
  (`run_healing.deterministic_run_id`) means the resubmission lands on the
  same run row — #501's `heal_failed_run_if_duplicate` flips it back to
  `created` with an accurate count instead of leaving failed-run litter.
- **Resubmission of already-succeeded batches**: a rerun (or an in-flight
  retry) of a batch whose items *all* have jobs and whose last run was not a
  failed one is answered by the server with a 400 `"No tasks were resolved"`
  — the all-duplicate branch of `create_run` (no failed run to heal, no new
  run worth creating). The tool recognizes that signal and treats the batch
  as a `created_count=0` success: the cursor advances and the loop continues
  with the next batch, instead of retrying a 400 that would never turn into
  a 2xx. This runs alongside the failed-run healing chain above — together
  they cover both "the batch died mid-flight" and "the batch already
  completed wholesale" on rerun.
- **Cursor discipline in the tool**: the manifest cursor only advances after
  a batch is accepted by the server (a 2xx, or the all-duplicate 400
  absorption above). A retry (or a full rerun after a crash) resubmits the
  batch wholesale and lets the server-side dedup skip what already exists.

Consequences worth knowing:

- The tool's `created_jobs` counter reports the server's `created_count`
  summed over accepted batches — i.e., *net new jobs this process created*,
  not items submitted. On a full rerun after a crash it correctly shows only
  the missing remainder (an absorbed all-duplicate batch contributes 0).
- Duplicated lines *within* the manifest are also deduplicated server-side
  (intra-request dedup uses the same key set).
- **Retry classification**: only 5xx responses and network errors
  (connection reset, timeout, backend restart) enter the retry loop.
  Deterministic 4xx failures — 401 (session expired mid-campaign; rerun the
  same command to resume, login is cheap), 403 (missing admin role or
  workspace membership), 422 (batch size over `max_items_per_run`, or an
  item contract violation in the manifest) — exit immediately with a hint,
  because retrying them cannot change the outcome. A 400 that is *not* the
  all-duplicate absorption signal (e.g. a partial-creation failure with
  committed-chunk progress in the detail) also exits immediately; rerunning
  the command heals those through the dedup + failed-run healing chain.

## 5. Suggested runbook flow

1. **Pre-flight**: workspace has a published workflow revision; fleet, DB
   pool, and retention settings match #349's campaign baseline table; the
   operator account has the admin role (the batch-size guard reads the
   admin-only instance-settings endpoint — non-admins exit 403 before the
   first batch).
2. **Dry-run**: `--dry-run` prints batch boundaries and the batch count;
   verifies the manifest parses and the guards pass (batch size vs
   `max_items_per_run`, watermark vs batch size) without touching the server.
3. **Submit**: run without `--dry-run` in `tmux`/`nohup`. Monitor the log
   lines (`水位 N >= M, 等 ...s 再查` when waiting; per-batch `run <id> ...
   新建 job N` on success).
4. **If it dies**: rerun the same command. See §4 for why this is safe.
5. **Verify completion**: the run list (`GET /workspaces/{id}/runs`) shows one
   run per batch; final job count = manifest size (assuming an empty
   workspace before the campaign).

## 6. Scope notes

- **Rerun-side campaigns are out of scope.** Re-running hundreds of thousands
  of *existing* jobs after a workflow/skill upgrade (batch-rerun /
  batch-run-to) is the same problem's second entry point with its own
  guardrail gaps (no slicing/throttling on the rerun path today); it needs a
  sibling mode against the rerun filter APIs and is tracked separately.
- The submitter talks only to public workspace APIs (`/api/auth/login`,
  `/api/workspaces/{id}/stats`, `/api/workspaces/{id}/runs`, admin
   instance-settings read for the batch-size guard); no new backend endpoints
  were added for this tool.
