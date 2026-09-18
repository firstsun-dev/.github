#!/usr/bin/env bash
# Validates .github/actions/normalize-branch-alias/normalize.sh against the
# Cloudflare Aliased Preview URL constraints.
#
# Usage: ./scripts/test-normalize-branch-alias.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NORMALIZE="$REPO_ROOT/.github/actions/normalize-branch-alias/normalize.sh"

fail=0

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" != "$actual" ]; then
    echo "FAIL: $desc — expected '$expected', got '$actual'"
    fail=1
  else
    echo "PASS: $desc"
  fi
}

assert_match() {
  local desc="$1" pattern="$2" actual="$3"
  if ! [[ "$actual" =~ $pattern ]]; then
    echo "FAIL: $desc — '$actual' does not match /$pattern/"
    fail=1
  else
    echo "PASS: $desc"
  fi
}

assert_le() {
  local desc="$1" max="$2" actual="$3"
  if [ "$actual" -gt "$max" ]; then
    echo "FAIL: $desc — length $actual exceeds max $max"
    fail=1
  else
    echo "PASS: $desc"
  fi
}

# --- Representative branch names from the spec ---

assert_eq "feat/calendar-ui" "feat-calendar-ui" "$("$NORMALIZE" "feat/calendar-ui")"
assert_eq "fix/muyu_audio" "fix-muyu-audio" "$("$NORMALIZE" "fix/muyu_audio")"
assert_eq "Feature/New.Page" "feature-new-page" "$("$NORMALIZE" "Feature/New.Page")"
assert_eq "123-starts-with-number" "branch-123-starts-with-number" "$("$NORMALIZE" "123-starts-with-number")"
assert_eq "123-new-page" "branch-123-new-page" "$("$NORMALIZE" "123-new-page")"

# --- Charset / shape invariants ---

for b in "feat/calendar-ui" "fix/muyu_audio" "Feature/New.Page" "123-starts-with-number" \
         "very-long-feature-branch-name-that-needs-truncation-aaaaaaaaaaaaaaaa"; do
  out="$("$NORMALIZE" "$b")"
  assert_match "charset for '$b'" '^[a-z][a-z0-9-]*$' "$out"
  assert_le "max length for '$b'" 32 "${#out}"
done

# --- Determinism ---

a1="$("$NORMALIZE" "feat/calendar-ui")"
a2="$("$NORMALIZE" "feat/calendar-ui")"
assert_eq "determinism (same branch, same alias)" "$a1" "$a2"

b1="$("$NORMALIZE" "very-long-feature-branch-name-that-needs-truncation-aaaaaaaaaaaaaaaa")"
b2="$("$NORMALIZE" "very-long-feature-branch-name-that-needs-truncation-aaaaaaaaaaaaaaaa")"
assert_eq "determinism (long branch, same alias)" "$b1" "$b2"

# --- Truncation collision safety: two long names differing only near the end ---

long_a="very-long-feature-branch-name-that-needs-truncation-aaaaaaaaaaaaaaaa"
long_b="very-long-feature-branch-name-that-needs-truncation-bbbbbbbbbbbbbbbb"
out_a="$("$NORMALIZE" "$long_a")"
out_b="$("$NORMALIZE" "$long_b")"
if [ "$out_a" = "$out_b" ]; then
  echo "FAIL: long branch names differing only near the end collided ('$out_a')"
  fail=1
else
  echo "PASS: long branch names differing only near the end do not collide ('$out_a' vs '$out_b')"
fi

# --- Empty / edge cases ---

assert_match "all-invalid-chars charset" '^[a-z][a-z0-9-]*$' "$("$NORMALIZE" "___...///")"

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "One or more assertions failed."
  exit 1
fi

echo ""
echo "All assertions passed."
