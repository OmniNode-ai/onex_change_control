#!/usr/bin/env bash
# audit_branch_protection.sh — Guard for branch-protection policy compliance.
# Referenced by:
#   omni_home/.github/workflows/scheduled-gap-detect.yml    (daily schedule)
#   omni_home/.github/workflows/branch-protection-guard.yml (PR gate, hard-fail)
#
# Audits BOTH `main` (the release boundary) AND `dev` (where daily merges land)
# for every OmniNode repo (OMN-14683). dev is the everyday merge target, so the
# solo-dev branch-protection invariants must hold there too — this is Rule #5
# (enforcement, not detection): a guard that only judges main leaves dev drift
# invisible.
#
# Per-branch checks (run for main AND dev):
#   1. Approving reviews are NOT enforced (solo dev — required reviews block PRs).
#      Judged via GraphQL `requiresApprovingReviews` (authoritative), NOT the
#      REST `required_pull_request_reviews` object. REST can report a phantom
#      `required_approving_review_count` even when reviews are not actually
#      enforced, which would false-fail dev; GraphQL reports the true state.
#   2. "CI Summary" is a required status check.
#   3. enforce_admins is true.
#
# Main-only / repo-level checks (unchanged — the release boundary is not weakened):
#   4. "verify / verify" Receipt Gate is a required status check
#      (main only, for repos in RECEIPT_GATE_REQUIRED_REPOS).
#      dev Receipt-Gate coverage is currently INCONSISTENT across repos, so dev
#      is surfaced as an informational NOTE (not asserted / not failed) and is
#      flagged for the operator in OMN-14683. Promote to a hard dev assertion
#      once every receipt-gate repo requires `verify / verify` on dev.
#   5. delete_branch_on_merge is true (repo setting — checked once per repo).
#   6. A "Merge Queue" ruleset exists (public repos — checked once per repo).
#
# DEV-EXEMPT repos (audited on `main` only): repos with no protected `dev` branch.
#   omnistream — no `dev` branch exists.
#   omniweb    — `dev` exists but is intentionally unprotected (PHP landing page).
#
# Exit 0 = all repos compliant.  Exit 1 = at least one deviation found.

set -euo pipefail

ORG="OmniNode-ai"

REPOS=(
  omniclaude
  omnimarket
  omnibase_compat
  omnibase_core
  omnibase_infra
  omnibase_spi
  omnidash
  omniintelligence
  omnimemory
  omninode_infra
  omnistream
  omniweb
  onex_change_control
)

if [[ -n "${BRANCH_PROTECTION_AUDIT_REPOS:-}" ]]; then
  IFS=',' read -r -a REPOS <<< "${BRANCH_PROTECTION_AUDIT_REPOS}"
fi

# Branches audited per repo. main is always audited; dev is audited unless the
# repo is dev-exempt (no protected dev branch). Override with a comma list.
BRANCHES=(main dev)
if [[ -n "${BRANCH_PROTECTION_AUDIT_BRANCHES:-}" ]]; then
  IFS=',' read -r -a BRANCHES <<< "${BRANCH_PROTECTION_AUDIT_BRANCHES}"
fi

# Private repos where Merge Queue rulesets are not expected
PRIVATE_REPOS=(omninode_infra omnistream omniweb)

# Repos with no protected `dev` branch — audited on `main` only.
DEV_EXEMPT_REPOS=(omnistream omniweb)

# Active repos that accept ticketed PRs must directly require the Receipt Gate.
# Do not treat CI Summary as an implicit substitute; the branch protection rule
# must expose the canonical `verify / verify` context so drift is visible.
RECEIPT_GATE_REQUIRED_REPOS=(
  omniclaude
  omnimarket
  omnibase_compat
  omnibase_core
  omnibase_infra
  omniintelligence
  omninode_infra
  onex_change_control
)

FAILURES=0
TOTAL_CHECKS=0
# Per-repo compliance flag (global; reset at the top of each check_repo).
REPO_OK=true

is_private() {
  local repo="$1"
  for p in "${PRIVATE_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

is_dev_exempt() {
  local repo="$1"
  for p in "${DEV_EXEMPT_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

requires_receipt_gate() {
  local repo="$1"
  for p in "${RECEIPT_GATE_REQUIRED_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

emit_jsonl() {
  local repo="$1"
  local branch="$2"
  local check="$3"
  local status="$4"
  local detail="$5"
  if [[ -z "${BRANCH_PROTECTION_AUDIT_JSONL:-}" ]]; then
    return
  fi
  jq -cn \
    --arg repo "$repo" \
    --arg branch "$branch" \
    --arg check "$check" \
    --arg status "$status" \
    --arg detail "$detail" \
    '{repo:$repo, branch:$branch, check:$check, status:$status, detail:$detail}' \
    >> "$BRANCH_PROTECTION_AUDIT_JSONL"
}

# Per-branch protection checks. Args: repo, branch, gql_rules_json
# Increments FAILURES and sets REPO_OK=false on any failure. Failures are
# attributed to the specific branch in both stdout and the JSONL stream.
check_branch() {
  local repo="$1"
  local branch="$2"
  local gql_rules="$3"
  local full="${ORG}/${repo}"

  echo "  ── branch: ${branch} ───────────────────"

  local protection
  protection=$(gh api "repos/${full}/branches/${branch}/protection" 2>&1) || {
    echo "    [${branch}] FAIL: Could not fetch branch protection (is it enabled?)"
    echo "             API response: ${protection}"
    emit_jsonl "$repo" "$branch" "branch_protection_fetch" "FAIL" "protection not fetchable"
    FAILURES=$((FAILURES + 1))
    REPO_OK=false
    return
  }

  # 1. Approving reviews must NOT be enforced (GraphQL-authoritative — avoids
  #    the REST required_pull_request_reviews phantom that false-fails dev).
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local requires_reviews
  # NOTE: do not use `first // "unknown"` — jq's `//` treats a boolean `false`
  # as empty, which would collapse the (valid) "reviews not enforced" answer
  # into "unknown". Branch on array length instead so false != missing.
  requires_reviews=$(printf '%s' "$gql_rules" | jq -r --arg b "$branch" '
    [ .data.repository.branchProtectionRules.nodes[]?
      | select(.pattern == $b)
      | .requiresApprovingReviews ]
    | if length == 0 then "unknown" else (.[0] | tostring) end
  ' 2>/dev/null || echo "unknown")
  if [[ "$requires_reviews" == "false" ]]; then
    echo "    [${branch}] PASS: approving reviews not enforced (GraphQL requiresApprovingReviews=false)"
    emit_jsonl "$repo" "$branch" "reviews_not_enforced" "PASS" "requiresApprovingReviews=false"
  elif [[ "$requires_reviews" == "true" ]]; then
    echo "    [${branch}] FAIL: approving reviews are enforced (blocks solo-dev merges)"
    emit_jsonl "$repo" "$branch" "reviews_not_enforced" "FAIL" "requiresApprovingReviews=true"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  else
    # GraphQL rule not found for this branch (or GraphQL unavailable). Do NOT
    # fall back to the phantom-prone REST count. Surface as a non-failing WARN.
    echo "    [${branch}] WARN: could not determine review enforcement via GraphQL (no matching rule)"
    emit_jsonl "$repo" "$branch" "reviews_not_enforced" "WARN" "GraphQL rule unavailable"
  fi

  # 2. "CI Summary" in required status checks
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local ci_summary
  ci_summary=$(printf '%s' "$protection" | jq -r '
    (
      (.required_status_checks.contexts // [])
      + ((.required_status_checks.checks // []) | map(.context))
    )
    | map(select(. == "CI Summary"))
    | length
  ')
  if [[ "$ci_summary" -ge 1 ]]; then
    echo "    [${branch}] PASS: \"CI Summary\" is a required status check"
    emit_jsonl "$repo" "$branch" "required_check_ci_summary" "PASS" "CI Summary required"
  else
    echo "    [${branch}] FAIL: \"CI Summary\" not found in required status checks"
    emit_jsonl "$repo" "$branch" "required_check_ci_summary" "FAIL" "CI Summary missing"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  fi

  # 3. enforce_admins is true
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local enforce
  enforce=$(printf '%s' "$protection" | jq -r '.enforce_admins.enabled // false')
  if [[ "$enforce" == "true" ]]; then
    echo "    [${branch}] PASS: enforce_admins is enabled"
    emit_jsonl "$repo" "$branch" "enforce_admins" "PASS" "enabled"
  else
    echo "    [${branch}] FAIL: enforce_admins is not enabled"
    emit_jsonl "$repo" "$branch" "enforce_admins" "FAIL" "disabled"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  fi

  # 4. "verify / verify" Receipt Gate — asserted on MAIN only. dev coverage is
  #    inconsistent across repos, so dev is informational (flagged, OMN-14683).
  if requires_receipt_gate "$repo"; then
    local receipt_gate
    receipt_gate=$(printf '%s' "$protection" | jq -r '
      (
        (.required_status_checks.contexts // [])
        + ((.required_status_checks.checks // []) | map(.context))
      )
      | map(select(. == "verify / verify"))
      | length
    ')
    if [[ "$branch" == "main" ]]; then
      TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
      if [[ "$receipt_gate" -ge 1 ]]; then
        echo "    [${branch}] PASS: \"verify / verify\" Receipt Gate is a required status check"
        emit_jsonl "$repo" "$branch" "required_check_receipt_gate" "PASS" "verify / verify required"
      else
        echo "    [${branch}] FAIL: \"verify / verify\" Receipt Gate not found in required status checks"
        emit_jsonl "$repo" "$branch" "required_check_receipt_gate" "FAIL" "verify / verify missing"
        REPO_OK=false
        FAILURES=$((FAILURES + 1))
      fi
    elif [[ "$receipt_gate" -ge 1 ]]; then
      echo "    [${branch}] NOTE: \"verify / verify\" Receipt Gate present (dev not asserted — informational)"
      emit_jsonl "$repo" "$branch" "required_check_receipt_gate" "NOTE" "present; dev not asserted"
    else
      echo "    [${branch}] NOTE: \"verify / verify\" Receipt Gate absent on dev (dev not asserted — flagged for operator, OMN-14683)"
      emit_jsonl "$repo" "$branch" "required_check_receipt_gate" "NOTE" "absent; dev not asserted (flagged)"
    fi
  fi
}

check_repo() {
  local repo="$1"
  local full="${ORG}/${repo}"
  REPO_OK=true

  echo "───────────────────────────────────────"
  echo "Repo: ${full}"
  echo "───────────────────────────────────────"

  # GraphQL branch-protection rules — the authoritative review-enforcement
  # signal, fetched once per repo and reused for every branch.
  local gql_rules
  # $owner/$name are GraphQL variables (bound via -f owner=/-f name=), not shell
  # expansions — they must stay literal inside the single-quoted query.
  # shellcheck disable=SC2016
  gql_rules=$(gh api graphql \
    -f query='query($owner:String!,$name:String!){ repository(owner:$owner,name:$name){ branchProtectionRules(first:50){ nodes{ pattern requiresApprovingReviews } } } }' \
    -f owner="$ORG" -f name="$repo" 2>&1) || gql_rules=""

  # ---------- Per-branch checks (main + dev) ----------
  local br
  for br in "${BRANCHES[@]}"; do
    if [[ "$br" == "dev" ]] && is_dev_exempt "$repo"; then
      echo "  ── branch: dev ───────────────────"
      echo "    [dev] SKIP: repo has no protected dev branch (dev-exempt)"
      emit_jsonl "$repo" "dev" "dev_branch_protection" "SKIP" "dev-exempt (no protected dev branch)"
      continue
    fi
    check_branch "$repo" "$br" "$gql_rules"
  done

  # ---------- Repo-level checks (once per repo — not branch-scoped) ----------
  # 5. delete_branch_on_merge
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local repo_settings
  repo_settings=$(gh api "repos/${full}" 2>&1) || {
    echo "  [repo] FAIL: Could not fetch repo settings"
    echo "         API response: ${repo_settings}"
    emit_jsonl "$repo" "-" "repo_settings_fetch" "FAIL" "repo settings not fetchable"
    FAILURES=$((FAILURES + 1))
    REPO_OK=false
    repo_settings=""
  }
  if [[ -n "$repo_settings" ]]; then
    local delete_branch
    delete_branch=$(printf '%s' "$repo_settings" | jq -r '.delete_branch_on_merge // false')
    if [[ "$delete_branch" == "true" ]]; then
      echo "  [repo] PASS: delete_branch_on_merge is true"
      emit_jsonl "$repo" "-" "delete_branch_on_merge" "PASS" "true"
    else
      echo "  [repo] FAIL: delete_branch_on_merge is not true"
      emit_jsonl "$repo" "-" "delete_branch_on_merge" "FAIL" "not true"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    fi
  fi

  # 6. Merge Queue ruleset (skip private repos)
  if is_private "$repo"; then
    echo "  [repo] SKIP: Merge Queue ruleset check (private repo)"
  else
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    local rulesets
    rulesets=$(gh api "repos/${full}/rulesets" 2>&1) || {
      echo "  [repo] FAIL: Could not fetch rulesets"
      echo "         API response: ${rulesets}"
      emit_jsonl "$repo" "-" "merge_queue_ruleset" "FAIL" "rulesets not fetchable"
      FAILURES=$((FAILURES + 1))
      REPO_OK=false
      rulesets=""
    }
    if [[ -n "$rulesets" ]]; then
      local mq_count
      mq_count=$(printf '%s' "$rulesets" | jq '[.[] | select(.name == "Merge Queue")] | length')
      if [[ "$mq_count" -ge 1 ]]; then
        echo "  [repo] PASS: \"Merge Queue\" ruleset exists"
        emit_jsonl "$repo" "-" "merge_queue_ruleset" "PASS" "exists"
      else
        echo "  [repo] FAIL: \"Merge Queue\" ruleset not found"
        emit_jsonl "$repo" "-" "merge_queue_ruleset" "FAIL" "not found"
        REPO_OK=false
        FAILURES=$((FAILURES + 1))
      fi
    fi
  fi

  if $REPO_OK; then
    echo "  >>> COMPLIANT"
  else
    echo "  >>> NON-COMPLIANT"
  fi
  echo ""
}

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
echo "======================================="
echo " Branch Protection Audit"
echo " Org: ${ORG}  |  Branches: ${BRANCHES[*]}"
echo " Dev-exempt (main only): ${DEV_EXEMPT_REPOS[*]}"
echo " Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "======================================="
echo ""

for repo in "${REPOS[@]}"; do
  check_repo "$repo"
done

echo "======================================="
echo " Summary: ${FAILURES} failure(s) across ${TOTAL_CHECKS} checks"
echo "======================================="

if [[ "$FAILURES" -gt 0 ]]; then
  exit 1
fi

echo "All repos compliant."
exit 0
