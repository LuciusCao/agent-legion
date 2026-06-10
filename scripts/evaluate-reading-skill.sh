#!/usr/bin/env bash
set -euo pipefail

skill_path="$1"
prompt="$2"
fixture_dir="$3"
output_dir="$4"
work_dir="$output_dir/work"
session_dir="$output_dir/session"

mkdir -p "$work_dir" "$session_dir"
cp "$fixture_dir"/*.json "$work_dir"/

args=(
  pi --mode json
  --session-dir "$session_dir"
  --name "reading-skill-eval"
  --no-context-files
  --no-extensions
  --no-prompt-templates
  --no-skills
  --tools read,write,bash
  --approve
)
if [[ "$skill_path" != "none" ]]; then
  args+=(--skill "$skill_path")
fi

(
  cd "$work_dir"
  "${args[@]}" "$prompt"
) >"$output_dir/events.jsonl" 2>"$output_dir/stderr.log"
