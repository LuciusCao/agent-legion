# Workflow Skills

The repository-owned Pi skills that used to live under this directory have been
migrated to standalone external git repositories.

## New Location

Each capability is now an independent repo under:

```text
~/.agents/skills/agent-legion/<workflow>/<capability>/
```

For example:

```text
~/.agents/skills/agent-legion/reading_analysis/extract_keywords/
~/.agents/skills/agent-legion/question_comprehension_info/generate_key_info/
```

## Migration

To migrate or re-migrate the skills from the current source tree, run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-skills-to-external-repos.py
```

The script is idempotent: it removes any existing target repository and recreates
it from the current source tree. Use `--push` and `--remote-template` to push to a
remote after the initial commit.

## Why External Repos?

Keeping each skill in its own repository lets the Pi executor clone skills on
demand, version them independently, and share them across workspaces without
bundling them into the Video Hive application repository.

## Legacy Resolver

`server/app/workflows/skills.py` still provides `resolve_workflow_skill` for
checking that a local skill directory contains the required contract files. It is
not removed by this migration.
