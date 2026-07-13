#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  echo "No Git worktree found at $ROOT_DIR" >&2
  exit 1
fi

chmod +x "$ROOT_DIR/.githooks/pre-commit" "$ROOT_DIR/.githooks/pre-push"
chmod +x "$ROOT_DIR/scripts/run-local-gate.sh"
COMMON_DIR="$(git -C "$ROOT_DIR" rev-parse --git-common-dir)"
if [[ "$COMMON_DIR" != /* ]]; then
  COMMON_DIR="$ROOT_DIR/$COMMON_DIR"
fi
HOOKS_DIR="$COMMON_DIR/hooks"
mkdir -p "$HOOKS_DIR"
cp "$ROOT_DIR/scripts/git-hooks/pre-commit" "$HOOKS_DIR/pre-commit"
cp "$ROOT_DIR/scripts/git-hooks/pre-push" "$HOOKS_DIR/pre-push"
chmod +x "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/pre-push"
git -C "$ROOT_DIR" config --unset-all core.hooksPath >/dev/null 2>&1 || true

echo "Installed shared Git hook dispatchers: $HOOKS_DIR"
echo "Dispatch target for this worktree: $ROOT_DIR/.githooks"
echo "  pre-commit: scripts/check-fast.sh"
echo "  pre-push feature branches: scripts/check-quick.sh"
echo "  pre-push protected branches/tags: scripts/check.sh"
