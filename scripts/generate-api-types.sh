#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
frontend_dir="${repo_dir}/frontend"
generated_dir="${frontend_dir}/src/generated"
mkdir -p "${generated_dir}"
temporary_dir="$(mktemp -d "${generated_dir}/.api-types.XXXXXX")"
schema_file="${temporary_dir}/openapi.json"
generated_file="${temporary_dir}/api.ts"
trap 'rm -rf "${temporary_dir}"' EXIT

cd "${repo_dir}"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.export_openapi "${schema_file}"

cd "${frontend_dir}"
./node_modules/.bin/openapi-typescript "${schema_file}" -o "${generated_file}"
./node_modules/.bin/prettier --config .prettierrc --write "${generated_file}"

if [[ "${1:-}" == "--check" ]]; then
  if ! cmp -s "${generated_file}" src/generated/api.ts; then
    echo "Generated API types are out of date. Run 'npm run api:generate'." >&2
    exit 1
  fi
elif [[ $# -eq 0 ]]; then
  mv "${generated_file}" src/generated/api.ts
else
  echo "Usage: $0 [--check]" >&2
  exit 2
fi
