#!/usr/bin/env bash
# Shared changed-path classification for lane trimming (#941, #917 T-1/T-2).
# Sourced by the CI `changes` job (.github/workflows/quality-gate.yml), the
# local quick gate (scripts/check-quick.sh) and the pre-push hook
# (.githooks/pre-push), so "what counts as docs" and "which velites files feed
# the Python side" live in exactly one place. Each caller still maps the
# remaining paths onto its own lanes (CI also has a docker lane, the local
# gates have a shared-files fallback); tests/scripts/test_lane_paths.py runs
# all three entry points over one path table to pin them in agreement.

# Documentation-only paths: docs/**, repository-root *.md and LICENSE. A
# bare `*.md` glob would also match nested markdown, and several of those are
# runtime inputs rather than docs (studio_chat/authoring_bootstrap.md is the
# Studio system prompt, mcp_server/*_guide.md are served MCP guides,
# examples/skills/**/SKILL.md are skill contents), so a change to them must
# run the lane that owns the directory.
lane_path_is_docs() {
  case "$1" in
    docs/* | LICENSE) return 0 ;;
    */*) return 1 ;;
    *.md) return 0 ;;
  esac
  return 1
}

# velites files that Python tests read directly (the event schema contract in
# tests/executors/test_velites_event_contract.py): besides the rust lane they
# must also run the backend lane.
lane_path_feeds_backend() {
  case "$1" in
    velites/schema/*) return 0 ;;
  esac
  return 1
}
