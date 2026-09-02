#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
#
# OMN-10414 (extends OMN-10347 / OMN-9730 DGM-Phase4): Mechanical block on ALL
# [skip-*] bypass tokens, including [skip-receipt-gate:] and [skip-deploy-gate:].
# Rejects any staged file or commit message containing [skip-<anything>:].
#
# BLOCKING — this hook rejects all [skip-*] tokens. This is the LOCAL enforcement
# layer. The GHA workflow (reject-deploy-gate-skip.yml) is the REMOTE enforcement
# layer. Both layers are required; neither is advisory-only. Using --no-verify
# bypasses this hook but not the remote GHA gate, which is a required status check.
#
# CLAUDE.md Rule #10: Never bypass local gates. Fix the underlying issue.
# Plan: omni_home/docs/plans/2026-04-30-gate-collapse-fix.md Task 8
#
# Tokens blocked (case-insensitive):
#   [skip-deploy-gate: ...]   — deploy-gate bypass (original OMN-9730)
#   [skip-receipt-gate: ...]  — receipt-gate bypass (OMN-10414)
#   [skip-<anything>: ...]    — any other [skip-*] form
#
# Escape hatch (explicit user approval only):
#   Add a line containing:  # skip-token-allowed: <receipt-id>
#   The receipt-id documents the explicit user approval hand-off.
#   This is NOT a free-text bypass — it requires a traceable approval receipt.
#
# Usage:
#   Invoked by pre-commit without filenames; it derives the staged/PR surface.
#   --exclude-regex <ERE>  Exempt matching paths in normal pre-commit mode.
#                         This is supplied explicitly by .pre-commit-config.yaml.
#   --self-test       Run synthetic self-tests and exit.
#   --check-pr-body <PR_NUMBER>   Also scan live PR body via gh cli.

set -euo pipefail

# OMN-10347: Broadened to ALL [skip-* tokens per Rule #10 (was [skip-deploy-gate: only).
SKIP_PATTERN='\[skip-[a-zA-Z]'
# Case-insensitive allowlist pattern — matches the skip-pattern's -i flag
ALLOWLIST_PATTERN='#[[:space:]]*[Ss][Kk][Ii][Pp]-[Tt][Oo][Kk][Ee][Nn]-[Aa][Ll][Ll][Oo][Ww][Ee][Dd]:[[:space:]]*[^[:space:]]'

RULE_REF="CLAUDE.md Rule #10 + docs/plans/2026-04-30-gate-collapse-fix.md Task 8"
TICKET_REF="OMN-10414"

# Pre-commit does not apply its `exclude` setting to an always-run hook with
# pass_filenames:false. Keep those exemptions as explicit hook arguments so the
# hook applies them to the internally derived candidate set. Flags are consumed
# before the legacy manual, self-test, and commit-message modes below.
EXCLUDE_REGEXES=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --exclude-regex)
            if [[ "$#" -lt 2 ]]; then
                echo "ERROR: --exclude-regex requires an extended regular expression." >&2
                exit 1
            fi
            EXCLUDE_REGEXES+=("$2")
            shift 2
            ;;
        --exclude-regex=*)
            EXCLUDE_REGEXES+=("${1#*=}")
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────────────
# Self-test mode
# ──────────────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--self-test" ]]; then
    PASS=0
    FAIL=0

    run_test() {
        local name="$1"
        local content="$2"
        local expect_exit="$3"

        # Use .md extension so the file-type filter includes it in scanning
        tmpfile=$(mktemp /tmp/skip-token-selftest.XXXXXX.md)
        printf '%s\n' "$content" > "$tmpfile"

        # Run hook against the temp file (not --self-test or --check-pr-body mode)
        actual_exit=0
        bash "$0" "$tmpfile" 2>/dev/null || actual_exit=$?

        rm -f "$tmpfile"

        if [[ "$actual_exit" == "$expect_exit" ]]; then
            echo "  PASS: $name"
            PASS=$((PASS + 1))
        else
            echo "  FAIL: $name (expected exit $expect_exit, got $actual_exit)"
            FAIL=$((FAIL + 1))
        fi
    }

    echo "=== reject-deploy-gate-skip-token.sh self-test ==="

    run_test "clean file passes" \
        "This is a clean PR body with no bypass tokens." \
        0

    run_test "skip-deploy-gate token rejected" \
        "[skip-deploy-gate: correctness fix, no deployable artifact change]" \
        1

    run_test "skip-receipt-gate free-text rejected (OMN-10414)" \
        "[skip-receipt-gate: docs only, no receipts needed]" \
        1

    run_test "skip-anything token rejected (OMN-10347)" \
        "[skip-anything: some reason]" \
        1

    run_test "skip-deploy-gate with allowlist receipt passes" \
        "[skip-deploy-gate: correctness fix]
# skip-token-allowed: USER-APPROVAL-2026-04-25-jonah" \
        0

    run_test "skip-receipt-gate with skip-token-allowed passes (OMN-10414)" \
        "[skip-receipt-gate: chore only]
# skip-token-allowed: USER-APPROVAL-2026-04-30-jonah" \
        0

    run_test "allowlist without skip-token passes" \
        "Normal PR body
# skip-token-allowed: some-receipt" \
        0

    run_test "case-insensitive skip-deploy-gate rejected" \
        "[Skip-Deploy-Gate: reason here]" \
        1

    run_test "case-insensitive skip-receipt-gate rejected (OMN-10414)" \
        "[Skip-Receipt-Gate: reason here]" \
        1

    echo ""
    echo "Results: $PASS passed, $FAIL failed"
    if [[ "$FAIL" -gt 0 ]]; then
        exit 1
    fi
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────────────
# PR body check mode
# ──────────────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--check-pr-body" ]]; then
    PR_NUMBER="${2:-}"
    if [[ -z "$PR_NUMBER" ]]; then
        echo "ERROR: --check-pr-body requires a PR number as the next argument" >&2
        exit 1
    fi

    if ! command -v gh &>/dev/null; then
        echo "WARNING: gh cli not available — skipping PR body check" >&2
        exit 0
    fi

    PR_BODY=$(gh pr view "$PR_NUMBER" --json body --jq .body 2>/dev/null || true)
    if [[ -z "$PR_BODY" ]]; then
        echo "WARNING: could not fetch PR body for PR #$PR_NUMBER — skipping" >&2
        exit 0
    fi

    if echo "$PR_BODY" | grep -qiE "$SKIP_PATTERN"; then
        if echo "$PR_BODY" | grep -qiE "$ALLOWLIST_PATTERN"; then
            echo "WARNING: [skip-*] token found in PR #$PR_NUMBER body but explicit approval receipt present — allowed." >&2
            exit 0
        fi
        echo "ERROR: PR #$PR_NUMBER body contains a [skip-*] bypass token." >&2
        echo "  Per $RULE_REF, bypass is not permitted without explicit user approval." >&2
        echo "  Fix the gate properly: add dod_evidence or use the structured no_deployable_artifact exception." >&2
        echo "  Ticket: $TICKET_REF" >&2
        exit 1
    fi
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────────────
# Commit-msg mode: invoked with a single argument pointing to COMMIT_EDITMSG.
# pre-commit passes the message file path; we read it directly (it is not a
# staged blob — it lives outside the index).
# ──────────────────────────────────────────────────────────────────────────────
if [[ "${GIT_HOOK_STAGE:-}" == "commit-msg" || "$#" -eq 1 && "${1:-}" == *COMMIT_EDITMSG* ]]; then
    msg_file="${1:-}"
    if [[ -n "$msg_file" && -f "$msg_file" ]]; then
        if grep -qiE "$SKIP_PATTERN" "$msg_file"; then
            if grep -qiE "$ALLOWLIST_PATTERN" "$msg_file"; then
                echo "WARNING: [skip-*] token found in commit message but explicit approval receipt present — allowed." >&2
                exit 0
            fi
            echo "ERROR: commit message contains a [skip-*] bypass token." >&2
            echo "  Per $RULE_REF, bypass is not permitted without explicit user approval." >&2
            echo "  Fix the gate properly:" >&2
            echo "    1. Add dod_evidence with type: no_deployable_artifact (preferred)" >&2
            echo "    2. Narrow the path patterns in validate_pr_deploy_required.py" >&2
            echo "    3. If truly exceptional, add '# skip-token-allowed: <receipt-id>' with a traceable approval receipt" >&2
            echo "  Ticket: $TICKET_REF" >&2
            exit 1
        fi
    fi
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────────────
# Manual mode: explicit filenames retain the legacy staged-blob behavior for
# direct troubleshooting. Production pre-commit mode does not enter this path;
# it has no filenames after parsing the explicit exclusion arguments above.
# ──────────────────────────────────────────────────────────────────────────────
FOUND_VIOLATION=0

scan_file() {
    local file="$1"

    # Restrict to PR-body-like file types to avoid false positives on source/test files
    case "$file" in
        *.md|*.yaml|*.yml|*.txt) ;;
        *) return ;;
    esac

    # Direct invocation is a manual/self-test convenience: prefer the staged
    # blob, then permit a local file. Normal pre-commit mode intentionally has
    # no working-tree fallback; see scan_normal_precommit_surface below.
    if git cat-file -e ":$file" 2>/dev/null; then
        staged_content="$(git show ":$file")"
    elif [[ -f "$file" ]]; then
        staged_content="$(<"$file")"
    else
        return
    fi

    if grep -qiE "$SKIP_PATTERN" <<< "$staged_content"; then
        # Check for explicit allowlist receipt in the staged content (also case-insensitive)
        if grep -qiE "$ALLOWLIST_PATTERN" <<< "$staged_content"; then
            echo "WARNING: [skip-*] token found in $file but explicit approval receipt present — allowed." >&2
            return
        fi

        echo "ERROR: $file contains a [skip-*] bypass token." >&2
        echo "  Per $RULE_REF, bypass is not permitted without explicit user approval." >&2
        echo "  Fix the gate properly:" >&2
        echo "    1. Add dod_evidence with type: no_deployable_artifact (preferred)" >&2
        echo "    2. For receipt-gate: add Evidence-Source + Evidence-Ticket to PR body and push OCC contract+receipts" >&2
        echo "    3. If truly exceptional, add '# skip-token-allowed: <receipt-id>' with a traceable approval receipt" >&2
        echo "  Ticket: $TICKET_REF" >&2
        FOUND_VIOLATION=1
    fi
}

# Explicit filenames are reserved for manual invocation and the built-in
# self-test. Pre-commit sets pass_filenames:false, so production execution
# reaches scan_normal_precommit_surface exactly once.
if [[ "$#" -gt 0 ]]; then
    for file in "$@"; do
        scan_file "$file"
    done
    exit "$FOUND_VIOLATION"
fi

# CI runs pre-commit with --all-files, which includes historical contracts that
# intentionally document removed skip-token forms. The hook therefore derives a
# bounded candidate set from the staged index plus the current branch surface.
# It then makes one index-wide Git grep call and intersects those NUL-safe paths
# in Bash. The number of Git subprocesses is constant with respect to path
# count; there is no cat-file/show/grep loop for individual files.

resolve_base_ref() {
    local upstream_ref=""
    local default_ref=""
    local explicit_ref="${GITHUB_BASE_REF:-}"

    if [[ -n "$explicit_ref" ]]; then
        case "$explicit_ref" in
            origin/*)
                printf '%s\n' "$explicit_ref"
                ;;
            refs/remotes/origin/*)
                printf 'origin/%s\n' "${explicit_ref#refs/remotes/origin/}"
                ;;
            refs/heads/*)
                printf 'origin/%s\n' "${explicit_ref#refs/heads/}"
                ;;
            *)
                printf 'origin/%s\n' "$explicit_ref"
                ;;
        esac
        return 0
    fi

    if upstream_ref="$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null)"; then
        if [[ -n "$upstream_ref" ]]; then
            printf '%s\n' "$upstream_ref"
            return 0
        fi
    fi

    if default_ref="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"; then
        if [[ "$default_ref" == origin/* ]]; then
            printf '%s\n' "$default_ref"
            return 0
        fi
    fi

    return 1
}

is_excluded_path() {
    local candidate_path="$1"
    local exclusion_regex=""

    for exclusion_regex in "${EXCLUDE_REGEXES[@]}"; do
        if [[ "$candidate_path" =~ $exclusion_regex ]]; then
            return 0
        fi
    done

    return 1
}

# Candidate and match membership are keyed by literal paths.  Linear array
# scans here would turn an adversarial all-files scan with many matching paths
# into O(matches² + matches*candidates) work. Associative arrays keep both
# operations constant-time per record while the ordered matched_paths array
# preserves deterministic diagnostic order.
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: reject-deploy-gate-skip-token.sh requires Bash 4+ for bounded path membership." >&2
    exit 1
fi
declare -A candidate_paths=()
declare -A matched_seen=()
declare -A matched_skip=()
declare -A matched_allowlist=()
matched_paths=()

add_match() {
    local matched_path="$1"
    local matched_line="$2"
    local has_skip=0
    local has_allowlist=0
    local nocasematch_was_set=0

    if shopt -q nocasematch; then
        nocasematch_was_set=1
    fi
    shopt -s nocasematch
    [[ "$matched_line" =~ $SKIP_PATTERN ]] && has_skip=1
    [[ "$matched_line" =~ $ALLOWLIST_PATTERN ]] && has_allowlist=1
    if [[ "$nocasematch_was_set" -eq 1 ]]; then
        shopt -s nocasematch
    else
        shopt -u nocasematch
    fi

    if [[ -n "${matched_seen["$matched_path"]+present}" ]]; then
        (( has_skip )) && matched_skip["$matched_path"]=1
        (( has_allowlist )) && matched_allowlist["$matched_path"]=1
        return 0
    fi

    matched_seen["$matched_path"]=1
    matched_paths+=("$matched_path")
    matched_skip["$matched_path"]="$has_skip"
    matched_allowlist["$matched_path"]="$has_allowlist"
}

if ! scan_directory="$(mktemp -d "${TMPDIR:-/tmp}/skip-token-scan.XXXXXX")"; then
    echo "ERROR: could not create a temporary scan directory; refusing to skip token enforcement." >&2
    exit 1
fi
trap 'rm -rf "$scan_directory"' EXIT

staged_paths_file="$scan_directory/staged-paths"
branch_paths_file="$scan_directory/branch-paths"
grep_matches_file="$scan_directory/grep-matches"

if ! base_ref="$(resolve_base_ref)"; then
    echo "ERROR: could not resolve PR base from GITHUB_BASE_REF, branch upstream, or origin/HEAD; refusing to skip token enforcement." >&2
    exit 1
fi
if ! base_oid="$(git rev-parse --verify --quiet --end-of-options "${base_ref}^{commit}")"; then
    echo "ERROR: resolved PR base ${base_ref} is unavailable locally; refusing to skip token enforcement." >&2
    exit 1
fi
if ! git merge-base "$base_oid" HEAD >/dev/null; then
    echo "ERROR: resolved PR base ${base_ref} (${base_oid}) has no common ancestor with HEAD; refusing to make an ambiguous scan." >&2
    exit 1
fi

# Do not fetch from a hook. A missing remote-tracking base or unrelated history
# is insufficient evidence for a bounded scan and fails closed above. A moved
# base with a common ancestor is valid for Git's base...HEAD comparison.
if ! git diff --cached --name-only -z > "$staged_paths_file"; then
    echo "ERROR: could not read staged changed paths; refusing to skip token enforcement." >&2
    exit 1
fi
if ! git diff --name-only -z "${base_oid}...HEAD" > "$branch_paths_file"; then
    echo "ERROR: could not read paths changed from ${base_ref}; refusing to skip token enforcement." >&2
    exit 1
fi

while IFS= read -r -d '' candidate_path; do
    candidate_paths["$candidate_path"]=1
done < "$staged_paths_file"
while IFS= read -r -d '' candidate_path; do
    candidate_paths["$candidate_path"]=1
done < "$branch_paths_file"

# This is the only production content scan. -z emits filename, line number,
# and line content with NUL separators for the first two fields, so newline
# filenames remain literal Bash array elements. Exit 1 means no match; every
# other Git failure must stop the blocking hook.
git_grep_status=0
if git grep --cached -z -n -i -E \
    -e "$SKIP_PATTERN" \
    -e "$ALLOWLIST_PATTERN" \
    -- '*.md' '*.yaml' '*.yml' '*.txt' > "$grep_matches_file"; then
    :
else
    git_grep_status=$?
    if [[ "$git_grep_status" -ne 1 ]]; then
        echo "ERROR: could not read indexed skip-token candidates; refusing to skip token enforcement." >&2
        exit 1
    fi
fi

while IFS= read -r -d '' matched_path; do
    if ! IFS= read -r -d '' matched_line_number || [[ ! "$matched_line_number" =~ ^[0-9]+$ ]]; then
        echo "ERROR: indexed skip-token scan returned malformed output; refusing to skip token enforcement." >&2
        exit 1
    fi
    matched_line=""
    if ! IFS= read -r matched_line && [[ -z "$matched_line" ]]; then
        echo "ERROR: indexed skip-token scan returned incomplete output; refusing to skip token enforcement." >&2
        exit 1
    fi
    add_match "$matched_path" "$matched_line"
done < "$grep_matches_file"

for matched_path in "${matched_paths[@]}"; do
    if [[ -z "${candidate_paths["$matched_path"]+present}" ]] || is_excluded_path "$matched_path"; then
        continue
    fi
    if [[ "${matched_skip["$matched_path"]}" -eq 0 ]]; then
        continue
    fi
    if [[ "${matched_allowlist["$matched_path"]}" -eq 1 ]]; then
        echo "WARNING: [skip-*] token found in $matched_path but explicit approval receipt present — allowed." >&2
        continue
    fi

    echo "ERROR: $matched_path contains a [skip-*] bypass token." >&2
    echo "  Per $RULE_REF, bypass is not permitted without explicit user approval." >&2
    echo "  Fix the gate properly:" >&2
    echo "    1. Add dod_evidence with type: no_deployable_artifact (preferred)" >&2
    echo "    2. For receipt-gate: add Evidence-Source + Evidence-Ticket to PR body and push OCC contract+receipts" >&2
    echo "    3. If truly exceptional, add '# skip-token-allowed: <receipt-id>' with a traceable approval receipt" >&2
    echo "  Ticket: $TICKET_REF" >&2
    FOUND_VIOLATION=1
done

exit "$FOUND_VIOLATION"
