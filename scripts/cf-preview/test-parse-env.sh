#!/usr/bin/env bash
# parse-env.mjs: caller-supplied KEY=VALUE text is data. Never evaluated, never able to set credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
P="$ROOT/actions/cf-preview/parse-env.mjs"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }

: > "$T/env"
VARS=$'PUBLIC_URL=https://x.example/?a=1&b=2\n# comment\n\nEVIL=$(touch '"$T"$'/pwned)`touch '"$T"$'/pwned2`;touch '"$T"$'/pwned3' node "$P" VARS "$T/env" >/dev/null
[ ! -e "$T/pwned" ] && [ ! -e "$T/pwned2" ] && [ ! -e "$T/pwned3" ] && ok "command substitution / backticks / ; are not executed" || bad "shell injection executed"
grep -qx 'PUBLIC_URL=https://x.example/?a=1&b=2' "$T/env" && ok "valid lines imported verbatim" || bad "valid import"
grep -q '^EVIL=\$(touch' "$T/env" && ok "metacharacters stay literal data" || bad "literal data"

: > "$T/env"
if VARS=$'OK=1\nCLOUDFLARE_API_TOKEN=stolen' node "$P" VARS "$T/env" >/dev/null 2>&1; then bad "credential override accepted"; else ok "credential override rejected"; fi
[ ! -s "$T/env" ] && ok "nothing written when any line is invalid (all-or-nothing)" || bad "partial write"
if VARS=$'OK=1\nGITHUB_ENV=/x' node "$P" VARS "$T/env" >/dev/null 2>&1; then bad "GITHUB_ENV override accepted"; else ok "GITHUB_ENV override rejected"; fi
if VARS=$'A=1\nB=2\nINJECT\nC=3' node "$P" VARS "$T/env" >/dev/null 2>&1; then bad "malformed line accepted"; else ok "malformed line rejected"; fi
# A value cannot smuggle a second variable through a newline: lines are split first, then each is validated.
: > "$T/env"
VARS=$'SAFE=1\nNODE_OPTIONS=--require /tmp/x' node "$P" VARS "$T/env" >/dev/null 2>&1 && bad "NODE_OPTIONS accepted" || ok "loader variable NODE_OPTIONS rejected"

# --mask prints ::add-mask:: for every value; new files are owner-only
rm -f "$T/sec"; umask 022
OUT="$(VARS=$'TOKEN=s3cr3t value' node "$P" VARS "$T/sec" --mask)"
echo "$OUT" | grep -qx '::add-mask::s3cr3t value' && ok "--mask registers values for log masking" || bad "mask"
[ "$(stat -c %a "$T/sec")" = 600 ] && ok "new secret file is owner-only (0600)" || bad "file mode $(stat -c %a "$T/sec")"
# --emit0 re-validates the file and prints NUL-separated pairs
[ "$(node "$P" --emit0 "$T/sec" | tr '\0' '|')" = 'TOKEN=s3cr3t value|' ] && ok "--emit0 round-trips values literally" || bad "emit0"
printf 'PATH=/evil\n' > "$T/tampered"; node "$P" --emit0 "$T/tampered" >/dev/null 2>&1 && bad "tampered file accepted" || ok "--emit0 rejects a tampered file"
# a symlinked tools checkout must not turn the importer into a silent no-op
ln -s "$ROOT/actions/cf-preview" "$T/link"; : > "$T/env"
VARS='VIA=symlink' node "$T/link/parse-env.mjs" VARS "$T/env" >/dev/null && grep -qx 'VIA=symlink' "$T/env" && ok "works through a symlinked path (no silent no-op)" || bad "symlink no-op"
exit $fail
