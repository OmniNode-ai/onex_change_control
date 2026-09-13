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
#   Local base policy: defaults to origin/dev, the repository integration branch.
#     For a declared stacked or promotion exception, configure the exact base:
#     git config --local branch.<feature-branch>.onexSkipTokenBase origin/<base-branch>
#   CI supplies ONEX_SKIP_TOKEN_EVENT and ONEX_SKIP_TOKEN_BASE from the checked
#     GitHub event payload. That authoritative base takes precedence; an absent,
#     malformed, zero-before, or forced-push base fails closed.
#   --self-test       Run synthetic self-tests and exit.
#   --check-pr-body <PR_NUMBER>   Also scan live PR body via gh cli.

set -euo pipefail

# OMN-10347: Broadened to ALL [skip-* tokens per Rule #10 (was [skip-deploy-gate: only).
SKIP_PATTERN='\[skip-[a-zA-Z]'
# Case-insensitive allowlist pattern — matches the skip-pattern's -i flag
ALLOWLIST_PATTERN='#[[:space:]]*[Ss][Kk][Ii][Pp]-[Tt][Oo][Kk][Ee][Nn]-[Aa][Ll][Ll][Oo][Ww][Ee][Dd]:[[:space:]]*[^[:space:]]'

RULE_REF="CLAUDE.md Rule #10 + docs/plans/2026-04-30-gate-collapse-fix.md Task 8"
TICKET_REF="OMN-10414"

# .github/workflows/guards.yml non-dev-base-guard makes dev the integration
# branch. Main promotion and declared stacked PRs are explicit exceptions, so
# neither a feature branch's upstream nor origin/HEAD is evidence of a base.
INTEGRATION_BASE_REF="origin/dev"

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

# Path identity must remain byte/case exact.  The content recognizers below
# temporarily enable nocasematch, but no inherited shell option may turn path
# membership, deduplication, or exclusions into case-insensitive comparisons.
shopt -u nocasematch

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
    local event_name="${ONEX_SKIP_TOKEN_EVENT:-}"
    local event_base="${ONEX_SKIP_TOKEN_BASE:-}"
    local push_forced="${ONEX_SKIP_TOKEN_PUSH_FORCED:-false}"
    local explicit_ref="${GITHUB_BASE_REF:-}"
    local current_branch=""
    local configured_ref=""
    local resolved_ref=""

    if [[ -n "$event_name" ]]; then
        case "$event_name" in
            pull_request|push|merge_group|workflow_dispatch) ;;
            *)
                echo "ERROR: unsupported authoritative skip-token event '$event_name'; refusing to infer a base." >&2
                return 1
                ;;
        esac
        if [[ -z "$event_base" ]]; then
            echo "ERROR: $event_name requires an explicit base; refusing to infer a detached or integration base." >&2
            return 1
        fi
        if [[ "$event_name" == "push" && "$push_forced" == "true" ]]; then
            echo "ERROR: forced push has no safe before-to-HEAD scan; refusing to infer a base." >&2
            return 1
        fi
        if [[ "$event_name" == "push" && "$event_base" =~ ^0{40}$ ]]; then
            echo "ERROR: push all-zero before SHA has no parent history; refusing to infer a base." >&2
            return 1
        fi
        if ! resolved_ref="$(normalize_base_ref "$event_base")"; then
            echo "ERROR: $event_name supplied a malformed authoritative base; refusing to infer a base." >&2
            return 1
        fi
        printf '%s\n' "$resolved_ref"
        return 0
    fi

    if [[ -n "$explicit_ref" ]]; then
        # GitHub supplies this from the actual PR metadata. It is also a
        # developer override in local hooks, so its normalized result receives
        # the same self-reference check as a configured branch base below.
        if ! normalize_base_ref "$explicit_ref"; then
            echo "ERROR: GITHUB_BASE_REF is malformed; refusing to infer a base." >&2
            return 1
        fi
        return 0
    fi

    current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [[ -z "$current_branch" ]]; then
        echo "ERROR: detached HEAD has no local branch base configuration; set GITHUB_BASE_REF to the PR base." >&2
        return 1
    fi
    if configured_ref="$(git config --get "branch.${current_branch}.onexSkipTokenBase" 2>/dev/null)"; then
        if ! resolved_ref="$(normalize_base_ref "$configured_ref")"; then
            echo "ERROR: configured local skip-token base is malformed; refusing to infer a base." >&2
            return 1
        fi
    else
        resolved_ref="$INTEGRATION_BASE_REF"
    fi
    if [[ -n "$current_branch" && "$resolved_ref" == "origin/$current_branch" && "$resolved_ref" != "$INTEGRATION_BASE_REF" ]]; then
        echo "ERROR: resolved base $resolved_ref is this feature branch; use its integration, promotion, or declared stacked PR base instead." >&2
        return 1
    fi

    printf '%s\n' "$resolved_ref"
}

normalize_base_ref() {
    local supplied_ref="$1"
    local normalized_ref=""
    local normalized_branch=""

    if [[ -z "$supplied_ref" || "$supplied_ref" =~ [[:space:]] ]]; then
        return 1
    fi

    case "$supplied_ref" in
        [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F])
            printf '%s\n' "$supplied_ref"
            return 0
            ;;
        origin/*)
            normalized_ref="$supplied_ref"
            ;;
        refs/remotes/origin/*)
            normalized_ref="origin/${supplied_ref#refs/remotes/origin/}"
            ;;
        refs/heads/*)
            normalized_ref="origin/${supplied_ref#refs/heads/}"
            ;;
        *)
            normalized_ref="origin/$supplied_ref"
            ;;
    esac

    normalized_branch="${normalized_ref#origin/}"
    # A remote's symbolic HEAD is mutable repository metadata, not a branch or
    # versioned object. Reject its direct and rev-parse-expression aliases
    # before any Git scan. The remaining rejects are invalid branch syntax or
    # revision operators that could turn an otherwise fixed branch into a
    # mutable pseudo-base (for example origin/HEAD^).
    if [[ "$normalized_branch" == "HEAD" || "$normalized_branch" == *".."* || "$normalized_branch" == *"//"* || "$normalized_branch" == /* || "$normalized_branch" == */ || "$normalized_branch" == .* || "$normalized_branch" == *. || "$normalized_branch" == "@" || "$normalized_branch" == *"@{"* || "$normalized_branch" == *"^"* || "$normalized_branch" == *"~"* || "$normalized_branch" == *":"* || "$normalized_branch" == *"?"* || "$normalized_branch" == *"*"* || "$normalized_branch" == *"["* || "$normalized_branch" == *"\\"* ]]; then
        return 1
    fi

    printf '%s\n' "$normalized_ref"
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

# Candidate and match membership are keyed by byte-exact literal paths. Bash
# 3.2 has no associative arrays, so encode each path as a hex-only variable
# suffix and use Bash builtins for a constant-time marker lookup. This stays
# O(M + N) in match/candidate records without per-path subprocesses, works
# under macOS's system Bash, and retains the ordered array for diagnostics.
LC_ALL=C
PATH_KEY=""
PATH_MARKER_NAME=""
matched_paths=()

build_path_key() {
    local path="$1"
    local path_index=0
    local path_byte=""
    local path_byte_hex=""

    PATH_KEY=""
    for ((path_index = 0; path_index < ${#path}; path_index++)); do
        path_byte="${path:path_index:1}"
        printf -v path_byte_hex '%02x' "'$path_byte"
        PATH_KEY="${PATH_KEY}${path_byte_hex}"
    done
}

build_path_marker_name() {
    local marker_kind="$1"
    local path="$2"

    build_path_key "$path"
    PATH_MARKER_NAME="omn17496_${marker_kind}_${PATH_KEY}"
}

mark_path() {
    build_path_marker_name "$1" "$2"
    printf -v "$PATH_MARKER_NAME" '%s' "1"
}

path_is_marked() {
    build_path_marker_name "$1" "$2"
    # PATH_MARKER_NAME has a fixed prefix and a hex-only suffix, so this
    # indirect expansion is safe for literal paths including whitespace and
    # newlines. The '-' default is required under set -u for absent markers.
    eval "[[ \${${PATH_MARKER_NAME}-} == 1 ]]"
}

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

    if path_is_marked "matched_seen" "$matched_path"; then
        if (( has_skip )); then
            mark_path "matched_skip" "$matched_path"
        fi
        if (( has_allowlist )); then
            mark_path "matched_allowlist" "$matched_path"
        fi
        return 0
    fi

    mark_path "matched_seen" "$matched_path"
    matched_paths+=("$matched_path")
    if (( has_skip )); then
        mark_path "matched_skip" "$matched_path"
    fi
    if (( has_allowlist )); then
        mark_path "matched_allowlist" "$matched_path"
    fi
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
    echo "ERROR: could not resolve an authoritative event or local policy base; refusing to skip token enforcement." >&2
    exit 1
fi
if ! resolved_oids="$(git rev-parse --quiet "${base_ref}^{commit}" HEAD^{commit} 2>/dev/null)"; then
    echo "ERROR: resolved PR base ${base_ref} is unavailable locally; refusing to skip token enforcement." >&2
    exit 1
fi
base_oid="${resolved_oids%%$'\n'*}"
head_oid="${resolved_oids#*$'\n'}"
if [[ "$resolved_oids" != *$'\n'* || ! "$base_oid" =~ ^[0-9a-f]{40}$ || ! "$head_oid" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: could not resolve immutable base and HEAD objects; refusing to skip token enforcement." >&2
    exit 1
fi

# A base equal to HEAD makes the committed candidate range empty. Reject that
# self-reference for every explicit/event base, including attached and detached
# 40-hex forms. The versioned local origin/dev policy remains usable while a
# developer prepares their first staged commit, and a push event's before SHA
# retains its distinct server-defined semantics. Resolve base and HEAD together
# above so this invariant costs no extra Git subprocess.
if [[ "$base_oid" == "$head_oid" && "${ONEX_SKIP_TOKEN_EVENT:-}" != "push" && ( -n "${ONEX_SKIP_TOKEN_EVENT:-}" || -n "${GITHUB_BASE_REF:-}" || "$base_ref" != "$INTEGRATION_BASE_REF" ) ]]; then
    echo "ERROR: resolved PR base ${base_ref} is HEAD; refusing an empty committed-change scan." >&2
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
    mark_path "candidate" "$candidate_path"
done < "$staged_paths_file"
while IFS= read -r -d '' candidate_path; do
    mark_path "candidate" "$candidate_path"
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
    if ! path_is_marked "candidate" "$matched_path" || is_excluded_path "$matched_path"; then
        continue
    fi
    if ! path_is_marked "matched_skip" "$matched_path"; then
        continue
    fi
    if path_is_marked "matched_allowlist" "$matched_path"; then
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
