# Local Quality Gates Without a CI Runner

## Purpose

This project does not currently have CI runner capacity. Local
quality gates therefore protect the exact commit that is pushed, while GitLab branch settings keep
the verified commit identity unchanged during merge.

This is a deliberate single-maintainer operating model, not a claim that local hooks provide the
same trust boundary as server-side CI. Hooks can be bypassed with `--no-verify`, so protected branch
settings and an explicit merge-request record remain required.

## Gate Levels

| Event | Gate | Command |
| --- | --- | --- |
| Commit | Fast | `scripts/check-fast.sh` |
| Push feature branch | Quick | `scripts/check-quick.sh` |
| Push `develop`, `main`, `master`, `release/*`, or a tag | Full | `scripts/check.sh` |
| Release, migration, concurrency, or recovery change | Extended | `scripts/check-ci.sh` manually |

Install the repository-managed hooks once from a worktree that contains `.githooks/`:

```bash
make install-hooks
```

The installer copies small dispatchers into the Git common hooks directory. A dispatcher resolves
the current worktree root and executes its versioned `.githooks/` implementation. If an older
branch does not contain `.githooks/`, the dispatcher exits successfully and leaves that worktree
unaffected. Passing evidence is shared through the same Git common directory.

## Exact-Commit Evidence

Before running a pre-push gate, `scripts/run-local-gate.sh` requires a clean worktree. A successful
result is stored under:

```text
<git-common-dir>/local-gates/<commit-sha>/<gate>-<fingerprint>.pass
```

The fingerprint includes the gate scripts, dependency lock files, architecture registries, and
local tool versions. Repeated pushes of the same unchanged commit reuse the evidence. Set
`AGENT_LEGION_LOCAL_GATE_FORCE=1` to run the gate again.

The evidence is intentionally local and is never committed. The merge request records only the
verified commit SHA and gate level.

## Required GitLab Settings

Configure the project in GitLab as follows:

1. Protect `develop` and any release branches.
2. Disable force-push and branch deletion for protected branches.
3. Use **Fast-forward merge** so GitLab does not create an unverified merge commit.
4. Merge changes through a merge request; do not edit protected branches in the Web IDE.
5. Keep the default merge-request template enabled and record the locally verified SHA.

Fast-forward-only history is essential: the commit entering `develop` must be the same commit that
passed the local full gate.

## Extended Gate Policy

Run `scripts/check-ci.sh` manually before pushing when changes touch any of these areas:

- PostgreSQL schema migration, offline SQLite import, backup, or restore;
- executor leases, capacity, cancellation, or worker concurrency;
- filesystem deletion, path validation, or artifact recovery;
- release tags or a large multi-branch integration.

If a local environment restriction prevents a deterministic test from running, record the exact
failure in the merge request and rerun in an environment that permits the required local resource.
Do not record passing evidence for a partial gate.

## Quality Impact

- Fast feedback remains cheap enough to run on every commit.
- Pushes are blocked unless the appropriate repository gate passes for the exact commit.
- Shared worktrees reuse evidence without sharing runtime data directories.
- GitLab keeps the tested SHA stable even without a Runner.
- The remaining trust limitation is explicit: local hooks cannot prevent a maintainer from using
  `--no-verify` or prove behavior on a second machine.
