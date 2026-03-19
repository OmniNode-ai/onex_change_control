#!/usr/bin/env bash
# audit_branch_protection.sh — Daily guard for branch-protection policy compliance.
# Referenced by: omni_home/.github/workflows/scheduled-gap-detect.yml
#
# Checks every OmniNode repo for:
#   1. required_pull_request_reviews is absent or null (solo dev — reviews block PRs)
#   2. "CI Summary" is a required status check
#   3. enforce_admins is true
#   4. delete_branch_on_merge is true
#   5. A "Merge Queue" ruleset exists (public repos only)
#
# Exit 0 = all repos compliant.  Exit 1 = at least one deviation found.

set -euo pipefail

ORG="OmniNode-ai"

# All 11 repos
REPOS=(
  omniclaude
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

# Private repos where Merge Queue rulesets are not expected
PRIVATE_REPOS=(omninode_infra omnistream omniweb)

BRANCH="main"
FAILURES=0
TOTAL_CHECKS=0

is_private() {
  local repo="$1"
  for p in "${PRIVATE_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

check_repo() {
  local repo="$1"
  local repo_ok=true
  local full="${ORG}/${repo}"

  echo "───────────────────────────────────────"
  echo "Repo: ${full}"
  echo "───────────────────────────────────────"

  # ---------- Fetch branch protection ----------
  local protection
  protection=$(gh api "repos/${full}/branches/${BRANCH}/protection" 2>&1) || {
    echo "  FAIL: Could not fetch branch protection (is it enabled?)"
    echo "        API response: ${protection}"
    FAILURES=$((FAILURES + 1))
    return
  }

  # 1. required_pull_request_reviews must be absent or null
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local reviews
  reviews=$(echo "$protection" | jq -r '.required_pull_request_reviews // empty')
  if [[ -n "$reviews" && "$reviews" != "null" ]]; then
    echo "  FAIL: required_pull_request_reviews is set (must be null)"
    repo_ok=false
    FAILURES=$((FAILURES + 1))
  else
    echo "  PASS: required_pull_request_reviews is null/absent"
  fi

  # 2. "CI Summary" in required status checks
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local ci_summary
  ci_summary=$(echo "$protection" | jq -r '
    .required_status_checks.contexts // [] | map(select(. == "CI Summary")) | length
  ')
  if [[ "$ci_summary" -ge 1 ]]; then
    echo "  PASS: \"CI Summary\" is a required status check"
  else
    echo "  FAIL: \"CI Summary\" not found in required status checks"
    repo_ok=false
    FAILURES=$((FAILURES + 1))
  fi

  # 3. enforce_admins is true
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local enforce
  enforce=$(echo "$protection" | jq -r '.enforce_admins.enabled // false')
  if [[ "$enforce" == "true" ]]; then
    echo "  PASS: enforce_admins is enabled"
  else
    echo "  FAIL: enforce_admins is not enabled"
    repo_ok=false
    FAILURES=$((FAILURES + 1))
  fi

  # ---------- Fetch repo settings ----------
  # 4. delete_branch_on_merge
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local repo_settings
  repo_settings=$(gh api "repos/${full}" 2>&1) || {
    echo "  FAIL: Could not fetch repo settings"
    FAILURES=$((FAILURES + 1))
    return
  }
  local delete_branch
  delete_branch=$(echo "$repo_settings" | jq -r '.delete_branch_on_merge // false')
  if [[ "$delete_branch" == "true" ]]; then
    echo "  PASS: delete_branch_on_merge is true"
  else
    echo "  FAIL: delete_branch_on_merge is not true"
    repo_ok=false
    FAILURES=$((FAILURES + 1))
  fi

  # 5. Merge Queue ruleset (skip private repos)
  if is_private "$repo"; then
    echo "  SKIP: Merge Queue ruleset check (private repo)"
  else
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    local rulesets
    rulesets=$(gh api "repos/${full}/rulesets" 2>&1) || {
      echo "  FAIL: Could not fetch rulesets"
      FAILURES=$((FAILURES + 1))
      repo_ok=false
      return
    }
    local mq_count
    mq_count=$(echo "$rulesets" | jq '[.[] | select(.name == "Merge Queue")] | length')
    if [[ "$mq_count" -ge 1 ]]; then
      echo "  PASS: \"Merge Queue\" ruleset exists"
    else
      echo "  FAIL: \"Merge Queue\" ruleset not found"
      repo_ok=false
      FAILURES=$((FAILURES + 1))
    fi
  fi

  if $repo_ok; then
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
echo " Org: ${ORG}  |  Branch: ${BRANCH}"
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
