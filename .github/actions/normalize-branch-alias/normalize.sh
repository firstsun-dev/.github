#!/usr/bin/env bash
# Normalizes a git branch name into a valid, deterministic Cloudflare
# Aliased Preview URL alias.
#
# Usage: normalize.sh <branch-name>
# Prints the normalized alias to stdout.
#
# Rules:
#   - lowercase only; charset restricted to a-z, 0-9, -
#   - invalid runs (/, _, ., spaces, etc.) collapse to a single -
#   - leading/trailing - stripped
#   - must start with a-z (prefixed with "branch-" otherwise)
#   - max length ~32 chars; overlong names are truncated and suffixed with
#     an 8-char sha256 hash of the ORIGINAL branch name (not the truncated
#     string) so that two long branches differing only near the end do not
#     collide.

set -euo pipefail

MAX_LEN=32
HASH_LEN=8

branch="${1:?Usage: $0 <branch-name>}"

norm=$(echo "$branch" | tr '[:upper:]' '[:lower:]')
norm=$(echo "$norm" | sed -E 's/[^a-z0-9]+/-/g')
norm=$(echo "$norm" | sed -E 's/-+/-/g')
norm=$(echo "$norm" | sed -E 's/^-+//; s/-+$//')

if [ -z "$norm" ]; then
  norm="branch"
fi

if ! [[ "$norm" =~ ^[a-z] ]]; then
  norm="branch-$norm"
fi

if [ "${#norm}" -gt "$MAX_LEN" ]; then
  hash=$(printf '%s' "$branch" | sha256sum | cut -c1-"$HASH_LEN")
  keep=$((MAX_LEN - 1 - HASH_LEN))
  prefix="${norm:0:$keep}"
  prefix=$(echo "$prefix" | sed -E 's/-+$//')
  norm="${prefix}-${hash}"
fi

printf '%s' "$norm"
