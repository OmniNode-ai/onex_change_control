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
#      EXCEPTION — REVIEW-GATED main (REVIEW_GATED_MAIN_REPOS, OMN-17437):
#      on onex_change_control main the invariant inverts — code-owner review
#      MUST be enforced (it is the grant registry's anti-self-issue anchor),
#      and its absence is the failure.
#   2. Required status checks are correct for the branch's ROLE:
#      - Ordinary PR-merge branches (all dev branches; main on repos that are
#        not release-synced) must require "CI Summary".
#      - RELEASE-SYNCED main (see RELEASE_SYNCED_MAIN_REPOS) must instead have
#        an EMPTY required_status_checks *and* an active ruleset restricting
#        updates to refs/heads/main with a non-empty bypass-actor set.
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
# MAIN-EXEMPT repos (audited on `dev` only): see MAIN_AUDIT_EXEMPT_REPOS.
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

# ──────────────────────────────────────────────────────────────────────
# RELEASE-SYNCED main (OMN-16289 / OMN-17186)
# ──────────────────────────────────────────────────────────────────────
# For these repos `main` is NOT a PR-merge target. It is the release
# boundary: release.yml fast-forwards main to the published tag's commit
# after a release succeeds. PRs land on dev.
#
# Consequence for this guard: `required_status_checks` on main is
# DELIBERATELY EMPTY. A PR-shaped context ("CI Summary", "verify / verify")
# can never report on a ref that is only ever advanced by an automated
# fast-forward, so a required context there does not gate anything — it only
# blocks the sync. Asserting "CI Summary" on these mains asserts an invariant
# OMN-16289 retired on purpose, and is why this guard was red on every
# omni_home PR from 2026-08-24.
#
# What actually protects these mains is a repository RULESET on
# refs/heads/main that restricts updates, with the release automation identity
# as a bypass actor. BOTH halves are asserted below and neither alone is
# sufficient: empty contexts without a ruleset is an unprotected main, and a
# ruleset without empty contexts is a main that cannot sync.
#
# NOT legacy branch-protection `restrictions`: OMN-16343 proved live (GH006,
# omnibase_core, run 32473543173, 2026-08-21) that restrictions:{apps:[...]}
# reads back correctly yet the protected-branch hook still declines the
# release sync push, silently desyncing main. `restrictions` stays null.
RELEASE_SYNCED_MAIN_REPOS=(
  omnibase_core
  omnibase_infra
  omnibase_spi
  omnimemory
  omnidash
  omniintelligence
  omnimarket
)

# Repos audited on `dev` only — `main` is not audited at all.
#
# omnibase_compat is a TEMPORARY repo (operator ruling 2026-08-21) and is
# explicitly excluded from branch-protection hardening, so neither invariant
# is asserted on its main.
#
# Do NOT "fix" this by moving it into RELEASE_SYNCED_MAIN_REPOS: its
# release.yml still syncs main with the workflow GITHUB_TOKEN persisted by
# actions/checkout, and OMN-16343 proved that token is not an authorizable
# identity for a restricted ref — applying the ruleset there would break its
# next release sync rather than protect anything.
MAIN_AUDIT_EXEMPT_REPOS=(omnibase_compat)

# ──────────────────────────────────────────────────────────────────────
# REVIEW-GATED main (OMN-17437)
# ──────────────────────────────────────────────────────────────────────
# onex_change_control main is the prod-promotion grant registry boundary.
# OMN-17437 (landed via OCC#7939, 2026-09-01) made the grant merge
# mechanically gated: branch protection on OCC main now REQUIRES
# code-owner review (requiresApprovingReviews=true,
# requiresCodeOwnerReviews=true, requiredApprovingReviewCount=0), so
# grants/prod_promotion_grants.yaml cannot merge without a CODEOWNERS
# approval — the anti-self-issue anchor that omni_home/CLAUDE.md rules
# 2a/12 assert. The review count stays 0, so paths without a CODEOWNERS
# owner (release/evidence merges) still flow without human review —
# solo-dev throughput is preserved everywhere except the grant registry.
#
# Consequence for this guard: the check-1 solo-dev assertion ("approving
# reviews must NOT be enforced") INVERTS on these mains. Enforcement here
# is the invariant, and its ABSENCE is the drift — losing it silently
# reopens the agent self-issue path OMN-17437 closed.
REVIEW_GATED_MAIN_REPOS=(onex_change_control)

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

is_release_synced_main() {
  local repo="$1"
  for p in "${RELEASE_SYNCED_MAIN_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

is_main_audit_exempt() {
  local repo="$1"
  for p in "${MAIN_AUDIT_EXEMPT_REPOS[@]}"; do
    if [[ "$p" == "$repo" ]]; then
      return 0
    fi
  done
  return 1
}

is_review_gated_main() {
  local repo="$1"
  for p in "${REVIEW_GATED_MAIN_REPOS[@]}"; do
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

# Release-synced `main` assertion (OMN-17186). Args: repo, protection_json
#
# Replaces the "CI Summary" / "verify / verify" required-context assertions for
# repos whose main is advanced only by release automation. Asserts BOTH halves
# of the replacement protection; a repo passes only if both hold.
#
#   a) required_status_checks on main is EMPTY.
#   b) an ACTIVE branch ruleset covers refs/heads/main, carries the `update`
#      rule (restrict who may advance the ref), and names at least one bypass
#      actor (the release automation identity).
#
# (b) deliberately requires a NON-EMPTY bypass_actors set: a ruleset that
# restricts updates with nobody allowed to bypass does not protect the release
# boundary, it freezes it — the next release would fail to sync main.
#
# The rulesets LIST endpoint does not return `rules` or `bypass_actors`, so
# each candidate ruleset is re-fetched by id.
check_release_synced_main() {
  local repo="$1"
  local protection="$2"
  local full="${ORG}/${repo}"

  # (a) required_status_checks must be empty.
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local ctx_count
  ctx_count=$(printf '%s' "$protection" | jq '
    (
      (.required_status_checks.contexts // [])
      + ((.required_status_checks.checks // []) | map(.context))
    )
    | unique | length
  ' 2>/dev/null || echo "-1")
  if [[ "$ctx_count" == "0" ]]; then
    echo "    [main] PASS: required_status_checks is empty (release-synced main)"
    emit_jsonl "$repo" "main" "release_synced_contexts_empty" "PASS" "0 required contexts"
  else
    echo "    [main] FAIL: release-synced main carries ${ctx_count} required status check(s) — a PR context cannot report on an automated fast-forward ref, it only blocks the sync"
    emit_jsonl "$repo" "main" "release_synced_contexts_empty" "FAIL" "${ctx_count} required contexts"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  fi

  # (b) an active ruleset must restrict updates to refs/heads/main and name a
  #     bypass actor.
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local rulesets
  rulesets=$(gh api "repos/${full}/rulesets" 2>&1) || {
    echo "    [main] FAIL: could not fetch rulesets"
    echo "             API response: ${rulesets}"
    emit_jsonl "$repo" "main" "release_synced_push_ruleset" "FAIL" "rulesets not fetchable"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
    return
  }

  local candidate_ids matched_id="" matched_actors=""
  candidate_ids=$(printf '%s' "$rulesets" | jq -r '
    .[]? | select(.enforcement == "active" and .target == "branch") | .id
  ' 2>/dev/null || true)

  local rid detail verdict
  while IFS= read -r rid; do
    [[ -z "$rid" ]] && continue
    detail=$(gh api "repos/${full}/rulesets/${rid}" 2>&1) || continue
    verdict=$(printf '%s' "$detail" | jq -r '
      if (((.conditions.ref_name.include // [])
             | any(. == "refs/heads/main" or . == "~DEFAULT_BRANCH")))
         and (((.conditions.ref_name.exclude // [])
             | any(. == "refs/heads/main")) | not)
         and (([.rules[]?.type] | any(. == "update")))
         and (((.bypass_actors // []) | length) > 0)
      then ((.bypass_actors | map("\(.actor_type):\(.actor_id):\(.bypass_mode)") | join(",")))
      else "" end
    ' 2>/dev/null || true)
    if [[ -n "$verdict" ]]; then
      matched_id="$rid"
      matched_actors="$verdict"
      break
    fi
  done <<< "$candidate_ids"

  if [[ -n "$matched_id" ]]; then
    echo "    [main] PASS: active ruleset ${matched_id} restricts updates to refs/heads/main (bypass actors: ${matched_actors})"
    emit_jsonl "$repo" "main" "release_synced_push_ruleset" "PASS" "ruleset ${matched_id}; bypass ${matched_actors}"
  else
    echo "    [main] FAIL: no active ruleset restricts updates to refs/heads/main with a non-empty bypass-actor set"
    echo "             main has no required contexts AND no push restriction — it is unprotected."
    emit_jsonl "$repo" "main" "release_synced_push_ruleset" "FAIL" "no active update-restricting ruleset with bypass actors"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  fi
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

  # 1. Review enforcement (GraphQL-authoritative — avoids the REST
  #    required_pull_request_reviews phantom that false-fails dev).
  #    Two roles, opposite invariants:
  #      - ordinary branches: approving reviews must NOT be enforced
  #        (solo dev — required reviews block PRs);
  #      - REVIEW-GATED main (REVIEW_GATED_MAIN_REPOS, OMN-17437): code-owner
  #        review MUST be enforced — it is the grant registry's
  #        anti-self-issue anchor, and its absence is the drift.
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local requires_reviews requires_codeowner
  # NOTE: do not use `first // "unknown"` — jq's `//` treats a boolean `false`
  # as empty, which would collapse the (valid) "reviews not enforced" answer
  # into "unknown". Branch on array length instead so false != missing.
  requires_reviews=$(printf '%s' "$gql_rules" | jq -r --arg b "$branch" '
    [ .data.repository.branchProtectionRules.nodes[]?
      | select(.pattern == $b)
      | .requiresApprovingReviews ]
    | if length == 0 then "unknown" else (.[0] | tostring) end
  ' 2>/dev/null || echo "unknown")
  requires_codeowner=$(printf '%s' "$gql_rules" | jq -r --arg b "$branch" '
    [ .data.repository.branchProtectionRules.nodes[]?
      | select(.pattern == $b)
      | .requiresCodeOwnerReviews ]
    | if length == 0 then "unknown" else (.[0] | tostring) end
  ' 2>/dev/null || echo "unknown")
  if [[ "$branch" == "main" ]] && is_review_gated_main "$repo"; then
    if [[ "$requires_reviews" == "true" && "$requires_codeowner" == "true" ]]; then
      echo "    [main] PASS: review-gated main — code-owner review enforced (OMN-17437 grant-registry anchor)"
      emit_jsonl "$repo" "main" "review_gated_main_enforced" "PASS" "requiresApprovingReviews=true requiresCodeOwnerReviews=true"
    elif [[ "$requires_reviews" == "unknown" ]]; then
      echo "    [main] WARN: could not determine review enforcement via GraphQL (no matching rule)"
      emit_jsonl "$repo" "main" "review_gated_main_enforced" "WARN" "GraphQL rule unavailable"
    else
      echo "    [main] FAIL: review-gated main — code-owner review NOT enforced (requiresApprovingReviews=${requires_reviews}, requiresCodeOwnerReviews=${requires_codeowner}); the OMN-17437 anti-self-issue gate on the grant registry has regressed"
      emit_jsonl "$repo" "main" "review_gated_main_enforced" "FAIL" "requiresApprovingReviews=${requires_reviews} requiresCodeOwnerReviews=${requires_codeowner}"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    fi
  elif [[ "$requires_reviews" == "false" ]]; then
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

  # 2. Required status checks — role-dependent.
  #    Release-synced main asserts the OMN-16289 replacement pair instead of
  #    the (retired) "CI Summary" required context.
  if [[ "$branch" == "main" ]] && is_release_synced_main "$repo"; then
    check_release_synced_main "$repo" "$protection"
  else
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
  #    On release-synced main the Receipt Gate is skipped for the same reason
  #    "CI Summary" is: no PR context can report on an automated fast-forward
  #    ref. dev remains the surface where the Receipt Gate is enforced.
  if [[ "$branch" == "main" ]] && is_release_synced_main "$repo"; then
    echo "    [main] SKIP: \"verify / verify\" Receipt Gate not asserted on release-synced main (no PR context can report there)"
    emit_jsonl "$repo" "main" "required_check_receipt_gate" "SKIP" "release-synced main"
  elif requires_receipt_gate "$repo"; then
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
    -f query='query($owner:String!,$name:String!){ repository(owner:$owner,name:$name){ branchProtectionRules(first:50){ nodes{ pattern requiresApprovingReviews requiresCodeOwnerReviews } } } }' \
    -f owner="$ORG" -f name="$repo" 2>&1) || gql_rules=""

  # ---------- Per-branch checks (main + dev) ----------
  local br
  for br in "${BRANCHES[@]}"; do
    if [[ "$br" == "main" ]] && is_main_audit_exempt "$repo"; then
      echo "  ── branch: main ───────────────────"
      echo "    [main] SKIP: main not audited (temporary repo — excluded from branch-protection hardening by operator ruling 2026-08-21; see OMN-17186)"
      emit_jsonl "$repo" "main" "main_branch_audit" "SKIP" "main-audit-exempt (temporary repo)"
      continue
    fi
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
echo " Main-exempt (dev only): ${MAIN_AUDIT_EXEMPT_REPOS[*]}"
echo " Release-synced main:    ${RELEASE_SYNCED_MAIN_REPOS[*]}"
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
