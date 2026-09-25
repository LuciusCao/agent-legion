## Summary

<!-- What does this PR change and why? Link the issue if one exists. -->

## Quality Impact

<!-- Effect on boundaries, concurrency, security, persistence, and test
     coverage. State "none" explicitly when not applicable. -->

## Verification

<!-- Commands you ran and their results, e.g.:
     - [ ] ./scripts/check-quick.sh
     - [ ] New/changed tests cover the change
-->

## Docs Impact

<!-- Does this PR change a fact that docs state as current? Check all that
     apply; the docs-terms gate (retired terminology + fact consistency:
     schema version, default storage backend) rejects drift, but it only
     knows the facts it asserts — prose claims still need your eyes:
-->
- [ ] Schema migration added or SCHEMA_VERSION bumped → updated the version
      statement + recent-migrations list in
      `docs/materials-storage-deployment.md` §3.3
- [ ] Default value / port / backend selection changed → updated both
      `README.md` and `README_EN.md` (and the relevant `docs/` pages)
- [ ] None of the above (docs unchanged by design)

## Notes for reviewers

<!-- Anything non-obvious: migrations, config changes, rollout order. -->
