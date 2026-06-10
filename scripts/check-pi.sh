#!/usr/bin/env bash
set -euo pipefail

command -v pi >/dev/null || {
  echo "pi is not installed" >&2
  exit 1
}

pi --version
pi --list-models
