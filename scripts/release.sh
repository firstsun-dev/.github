#!/usr/bin/env bash
# Usage: ./scripts/release.sh v1.2.0
#
# Creates an exact tag (v1.2.0) for npm package pinning,
# then moves the floating major tag (v1) for workflow consumers.

set -euo pipefail

VERSION="${1:?Usage: $0 <version> e.g. v1.2.0}"

if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: version must be in vX.Y.Z format (got: $VERSION)" >&2
  exit 1
fi

MAJOR="v$(echo "$VERSION" | cut -d. -f1 | tr -d v)"  # e.g. v1
SEMVER="${VERSION#v}"                                   # e.g. 1.2.0

echo "Preparing release $VERSION (floating tag: $MAJOR)..."

# Bump package.json versions
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$SEMVER\"/" \
  packages/eslint-config/package.json \
  packages/tsconfig/package.json

echo "Updated package versions to $SEMVER"
git diff --stat

read -rp "Commit, tag $VERSION, and move $MAJOR? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

git add packages/eslint-config/package.json packages/tsconfig/package.json
git commit -m "chore: release $VERSION"

# Exact tag — for npm package pinning (immutable)
git tag "$VERSION"

# Floating major tag — for workflow consumers (moves forward each release)
git tag -f "$MAJOR"

echo ""
echo "Done. Push with:"
echo "  git push && git push origin $VERSION && git push origin $MAJOR --force"
