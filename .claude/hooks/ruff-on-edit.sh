#!/usr/bin/env bash
# PostToolUse hook: format and auto-fix Python files edited by Claude.
# Skips silently until the project has ruff installed (pyproject + uv env).
set -euo pipefail

file="$(jq -r '.tool_input.file_path // empty')"
[[ "$file" == *.py && -f "$file" ]] || exit 0

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
[[ -f "$root/pyproject.toml" ]] || exit 0
cd "$root"
uv run --quiet --no-sync ruff --version >/dev/null 2>&1 || exit 0

uv run --quiet --no-sync ruff format "$file" >/dev/null
if ! out="$(uv run --quiet --no-sync ruff check --fix "$file" 2>&1)"; then
  echo "ruff found issues it could not auto-fix in $file:" >&2
  echo "$out" >&2
  exit 2  # feed remaining lint errors back to Claude
fi
