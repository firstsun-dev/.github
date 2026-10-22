#!/usr/bin/env bash
# Source this file. load_env_file <file> exports NAME=VALUE pairs from a file written by parse-env.mjs.
# Values are DATA: they are re-validated by parse-env.mjs and exported through a quoted variable, never
# sourced, eval'ed or expanded (`$(...)`, backticks, quotes, `;` stay literal).
load_env_file() {
  local f="${1:-}" tmp kv
  [ -n "$f" ] && [ -f "$f" ] || return 0
  tmp="$(mktemp)"
  if ! node "${TOOLS_DIR:?}/parse-env.mjs" --emit0 "$f" --reserved > "$tmp"; then rm -f "$tmp"; return 1; fi
  while IFS= read -r -d '' kv; do export "${kv%%=*}=${kv#*=}"; done < "$tmp"
  rm -f "$tmp"
}
