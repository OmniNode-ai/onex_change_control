#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

# Test suite for check_ts_stubs.sh
#
# Usage: bash tests/test_check_ts_stubs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="$SCRIPT_DIR/../scripts/check_ts_stubs.sh"
TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

pass=0
fail=0

run_test() {
    local name="$1"
    local expected_exit="$2"
    shift 2
    # remaining args are files to pass to the detector

    local actual_exit=0
    "$DETECTOR" "$@" > /dev/null 2>&1 || actual_exit=$?

    if [[ $actual_exit -eq $expected_exit ]]; then
        echo "PASS: $name"
        pass=$((pass + 1))
    else
        echo "FAIL: $name (expected exit $expected_exit, got $actual_exit)"
        fail=$((fail + 1))
    fi
}

# --- Test 1: Forbidden stub (lowercase) ---
f="$TMPDIR_BASE/test1.ts"
echo '// stub: needs implementation' > "$f"
run_test "forbidden stub (lowercase)" 1 "$f"

# --- Test 2: Forbidden stub (mixed case) ---
f="$TMPDIR_BASE/test2.ts"
echo '// Stub: test' > "$f"
run_test "forbidden stub (mixed case)" 1 "$f"

# --- Test 3: Forbidden stub (uppercase) ---
f="$TMPDIR_BASE/test3.ts"
echo '// STUB: test' > "$f"
run_test "forbidden stub (uppercase)" 1 "$f"

# --- Test 4: Allowed stub-ok ---
f="$TMPDIR_BASE/test4.ts"
echo '// stub-ok: intentional no-op' > "$f"
run_test "allowed stub-ok" 0 "$f"

# --- Test 5: Allowed intentional-skip ---
f="$TMPDIR_BASE/test5.ts"
echo '// intentional-skip: command not projectable' > "$f"
run_test "allowed intentional-skip" 0 "$f"

# --- Test 6: No stubs ---
f="$TMPDIR_BASE/test6.ts"
echo 'const x = 42;' > "$f"
run_test "no stubs" 0 "$f"

# --- Test 7: Non-TS file ignored ---
f="$TMPDIR_BASE/test7.py"
echo '# stub: python stub' > "$f"
run_test "non-TS file ignored" 0 "$f"

# --- Test 8: node_modules skipped ---
mkdir -p "$TMPDIR_BASE/node_modules/pkg"
f="$TMPDIR_BASE/node_modules/pkg/index.ts"
echo '// stub: should be skipped' > "$f"
run_test "node_modules skipped" 0 "$f"

# --- Test 9: JSX file detected ---
f="$TMPDIR_BASE/test9.jsx"
echo '// stub: react component stub' > "$f"
run_test "JSX file with stub detected" 1 "$f"

# --- Test 10: TSX file detected ---
f="$TMPDIR_BASE/test10.tsx"
echo '// stub: react component stub' > "$f"
run_test "TSX file with stub detected" 1 "$f"

# --- Test 11: Mixed - stub-ok on one line, stub on another ---
f="$TMPDIR_BASE/test11.ts"
cat > "$f" <<'INNER'
// stub-ok: this is fine
const a = 1;
// stub: this is NOT fine
INNER
run_test "mixed stub-ok and stub" 1 "$f"

# --- Test 12: stub with extra whitespace ---
f="$TMPDIR_BASE/test12.ts"
echo '//  stub:  extra spaces' > "$f"
run_test "stub with extra whitespace" 1 "$f"

# --- Summary ---
echo ""
echo "Results: $pass passed, $fail failed"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
exit 0
