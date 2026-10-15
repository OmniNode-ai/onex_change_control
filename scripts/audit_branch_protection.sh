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
#   1. Approving reviews are NOT enforced (solo dev — required reviews block PRs),
#      except on the explicitly review-gated main repos below (main only —
#      OMN-18346 narrows the OMN-18287 dev extension back out: the operator
#      ruling at docs/tracking/ROLLING_WORK_LEDGER.md:7452 removed
#      required_pull_request_reviews from onex_change_control@dev on
#      2026-09-13, so dev is now correctly solo-dev and main alone stays
#      codeowner-gated; see omni_home CLAUDE.md rule 12). Judged via
#      GraphQL (authoritative), NOT the REST `required_pull_request_reviews`
#      object. REST can report a phantom `required_approving_review_count` even
#      when reviews are not actually enforced, which would false-fail dev;
#      GraphQL reports the true state.
#   2. Required status checks are correct for the branch's ROLE:
#      - Ordinary PR-merge branches (all dev branches; main on repos that are
#        not release-synced) must require "CI Summary".
#      - RELEASE-SYNCED main (see RELEASE_SYNCED_MAIN_REPOS) must instead have
#        an EMPTY required_status_checks *and* an active ruleset restricting
#        updates to refs/heads/main with a non-empty bypass-actor set.
#   3. enforce_admins is true.
#   3a. Every required_status_checks.checks[] entry names a reporting App
#       (app_id is not null) — OMN-19511. A null app_id is GitHub's "Any
#       source", satisfiable by any App or Action with checks:write, not just
#       the intended CI producer.
#
# Main-only / repo-level checks (unchanged — the release boundary is not weakened):
#   4. "verify / verify" Receipt Gate is a required status check
#      (main only, for repos in RECEIPT_GATE_REQUIRED_REPOS).
#      dev Receipt-Gate coverage is currently INCONSISTENT across repos, so dev
#      is surfaced as an informational NOTE (not asserted / not failed) and is
#      flagged for the operator in OMN-14683. Promote to a hard dev assertion
#      once every receipt-gate repo requires `verify / verify` on dev.
#   4a. "Lane Identity Gate" is a required status check (DEV only, for repos in
#      LANE_IDENTITY_GATE_REQUIRED_REPOS — OMN-18288). Asserted rather than
#      noted: the context has one producing repo and one reportable branch, so
#      there is no cross-repo inconsistency to be tolerant of.
#   5. delete_branch_on_merge is true (repo setting — checked once per repo).
#   6. A "Merge Queue" ruleset exists (public repos — checked once per repo).
#   6a. Every ENFORCED ruleset carrying a merge_queue rule has an EMPTY
#       bypass-actor list, read from the ruleset detail (OMN-19929). A bypass
#       actor lets that identity merge outside the queue (omnibase_infra#4197,
#       2026-09-28). A detail with no bypass_actors key (a token that cannot
#       administer the repo) or a detail that cannot be fetched FAILS: an
#       unreadable bypass list is not evidence of no bypass. The audit token
#       must therefore be able to read bypass lists.
#
# Repo-level checks 7 and 8 (OMN-16289 re-affirmed by the operator 2026-10-10,
# "Dev as a default branch has been the rule for months anything targeting Main
# has been violating that rule"):
#   7. The repository's default branch is `dev`. Judged for every repo in REPOS
#      and for the registry code repos in DEFAULT_BRANCH_ONLY_REPOS. A repo that
#      still defaults to `main` is tolerated only while it is named in
#      DEFAULT_BRANCH_PENDING_REPOS, a shrink-only baseline: an entry whose repo
#      already defaults to `dev` FAILS as stale, so the list can only get shorter.
#   8. No open feature pull request targets `main` in a repo whose default is
#      `dev`. A head branch under hotfix/ or promotion/ is a sanctioned main
#      path (prod-grant hotfixes, release promotion) and passes. Anything else
#      FAILS unless it is named in MAIN_PR_PENDING, a shrink-only baseline of
#      <repo>#<number> entries; an entry whose PR is no longer open against
#      `main` FAILS as stale.
# Docs-only repositories (knowledge-base, knowledge-base-internal) are outside
# both scopes: they hold no code and their readers consume origin/main by path.
#
# DEV-EXEMPT repos (audited on `main` only): repos with no protected `dev` branch.
#   omniweb    — `dev` exists but is intentionally unprotected (PHP landing page).
#   (omnistream left this list on 2026-10-10: its `dev` now exists, carries
#   main's protection and is its default branch.)
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
DEV_EXEMPT_REPOS=(omniweb)

# Registry code repos judged ONLY on checks 7 and 8 (default branch is `dev`, no
# feature PR targets `main`). Their branch protection is not audited here: the
# per-branch checks assume the CI Summary / ruleset shapes of the REPOS above.
# Move a repo into REPOS in the change that gives it that protection shape.
DEFAULT_BRANCH_ONLY_REPOS=(
  omniopencode
  omnicrush
  omnigemini
  omnibase_internal
  omniclaude-internal
  omni_home
)
if [[ -n "${BRANCH_PROTECTION_AUDIT_DEFAULT_ONLY_REPOS+x}" ]]; then
  IFS=',' read -r -a DEFAULT_BRANCH_ONLY_REPOS <<< "${BRANCH_PROTECTION_AUDIT_DEFAULT_ONLY_REPOS}"
elif [[ -n "${BRANCH_PROTECTION_AUDIT_REPOS:-}" ]]; then
  # A single-repo run (the tests, a spot check) does not drag the registry in.
  DEFAULT_BRANCH_ONLY_REPOS=()
fi

# SHRINK-ONLY baseline of repos still on a `main` default (OMN-16289). Each
# needs the preparation wave in beta/plans/2026-10-05-dev-default-branch-plan.md
# (workflow PR filters, sync timers, the stranded-clone guard) before the switch;
# delete the entry in the change that switches the repo. A new entry is refused
# in review: a repo that defaults to `main` is the defect this list counts down.
# Entries are read token-by-token by tests/test_audit_branch_protection_dev_default.py
# -- keep comments OUT of the array body.
DEFAULT_BRANCH_PENDING_REPOS=(
  omnibase_internal
  omniclaude-internal
  omni_home
)
if [[ -n "${BRANCH_PROTECTION_AUDIT_DEFAULT_PENDING_REPOS+x}" ]]; then
  IFS=',' read -r -a DEFAULT_BRANCH_PENDING_REPOS <<< "${BRANCH_PROTECTION_AUDIT_DEFAULT_PENDING_REPOS}"
elif [[ -n "${BRANCH_PROTECTION_AUDIT_REPOS:-}" ]]; then
  DEFAULT_BRANCH_PENDING_REPOS=()
fi

# SHRINK-ONLY baseline of open PRs that target `main` in a dev-default repo and
# are not hotfix/ or promotion/ heads. omniweb#437-439 repair omniweb `main`
# itself (the stale public-cluster deploy job, OMN-18635 AC6); `dev` already
# carries the fix, so retargeting them would be wrong. They leave this list when
# omniweb's main is reconciled (OMN-18635 AC8/AC9) or the PRs close.
# Entries are <repo>#<number>; keep comments OUT of the array body.
MAIN_PR_PENDING=(
  omniweb#437
  omniweb#438
  omniweb#439
)
if [[ -n "${BRANCH_PROTECTION_AUDIT_MAIN_PR_PENDING+x}" ]]; then
  IFS=',' read -r -a MAIN_PR_PENDING <<< "${BRANCH_PROTECTION_AUDIT_MAIN_PR_PENDING}"
elif [[ -n "${BRANCH_PROTECTION_AUDIT_REPOS:-}" ]]; then
  MAIN_PR_PENDING=()
fi

# Head-branch prefixes that are a sanctioned way to target `main` (prod-grant
# hotfix PRs, release promotion): omni_home CLAUDE.md rule 12 and the landing
# controller's retarget_of exemption.
SANCTIONED_MAIN_HEAD_REGEX='^(hotfix|promotion)/'

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
#
# OMN-16642 added omniclaude, the last repository still on the legacy
# manual-promotion-PR path to main. Its release.yml now carries the same
# mint + REST fast-forward pair as the seven above, its main
# required_status_checks are empty, and an active ruleset restricts updates to
# refs/heads/main with onexbot-occ-writer as the only bypass actor. With that
# move omninode_infra is the only ordinary-main repo left in REPOS, which is
# why the test fixture for the ordinary-main assertion names it.
#
# Entries here are read token-by-token by
# tests/test_audit_branch_protection_release_synced.py -- keep comments OUT of
# the array body.
RELEASE_SYNCED_MAIN_REPOS=(
  omnibase_core
  omnibase_infra
  omnibase_spi
  omnimemory
  omnidash
  omniintelligence
  omnimarket
  omniclaude
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

# Main branches where code-owner review is the deliberate anti-self-issue
# anchor for a sensitive governance file. These repos are not release-synced:
# their main branches retain ordinary required-context checks, but this review
# requirement overrides the solo-dev invariant above.
#
# main-only, not "every audited branch" (OMN-18346, narrowing OMN-18287):
# OMN-18287 widened this to dev on the premise that onex_change_control's dev
# carried the identical codeowner-only review model as main. That premise no
# longer holds — under the operator ruling at
# docs/tracking/ROLLING_WORK_LEDGER.md:7452 ("OCC tickets are mechanical, like
# they're supposed to be"), required_pull_request_reviews was removed from
# onex_change_control@dev on 2026-09-13 (dev: reviews null; main: unchanged,
# require_code_owner_reviews=true). See omni_home CLAUDE.md rule 12's
# "verified: 2026-09-13" paragraph for the live branch-protection readback.
# Re-widening this to dev would false-FAIL the now-correct, ruled state; dev
# reviews being enforced again would itself be the 2026-09-04 mistake rule 12
# warns against re-committing.
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

# ──────────────────────────────────────────────────────────────────────
# Lane Identity Gate on `dev` (OMN-18288)
# ──────────────────────────────────────────────────────────────────────
# The gate reported on omniclaude`s dev from 2026-09-13 and could not block
# anything: it was absent from required_status_checks, which is advisory
# detection and is the gap omni_home CLAUDE.md rule 5 names directly.
#
# DEV ONLY, and the asymmetry is deliberate rather than an omission. omniclaude
# is a release-synced-main repo, so its main carries an EMPTY
# required_status_checks by design (OMN-16289 / OMN-16642) and a PR-shaped
# context asserted there would block the release sync rather than gate
# anything -- the same reason the Receipt Gate is skipped on release-synced
# main above.
#
# ONE REPO, and it stays one until a second repo carries the gate. The module
# it protects (scripts/lane_identity.py) lives only in omniclaude, so listing a
# repo here that does not produce the context would assert a context that can
# never report and wedge every pull request in it. Add a repo to this list in
# the same change that gives it the workflow, never before.
LANE_IDENTITY_GATE_REQUIRED_REPOS=(omniclaude)

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

requires_lane_identity_gate() {
  local repo="$1"
  for p in "${LANE_IDENTITY_GATE_REQUIRED_REPOS[@]}"; do
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

  # 1. Review enforcement is GraphQL-authoritative. Ordinary branches retain
  #    the solo-dev invariant; explicitly review-gated mains require both
  #    approving and code-owner reviews. main-only (OMN-18346, narrowing the
  #    OMN-18287 dev extension — see REVIEW_GATED_MAIN_REPOS comment above).
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  if [[ "$branch" == "main" ]] && is_review_gated_main "$repo"; then
    local review_state
    review_state=$(printf '%s' "$gql_rules" | jq -r --arg b "$branch" '
      [ .data.repository.branchProtectionRules.nodes[]?
        | select(.pattern == $b)
        | "\(.requiresApprovingReviews | tostring):\(.requiresCodeOwnerReviews | tostring)" ]
      | if length == 0 then "unknown" else .[0] end
    ' 2>/dev/null || echo "unknown")
    if [[ "$review_state" == "true:true" ]]; then
      echo "    [${branch}] PASS: approving and code-owner reviews are enforced (review-gated main)"
      emit_jsonl "$repo" "$branch" "reviews_required" "PASS" "requiresApprovingReviews=true; requiresCodeOwnerReviews=true"
    else
      echo "    [${branch}] FAIL: review-gated main requires approving and code-owner reviews (GraphQL state=${review_state})"
      emit_jsonl "$repo" "$branch" "reviews_required" "FAIL" "requiresApprovingReviews/requiresCodeOwnerReviews=${review_state}"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    fi
  else
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

  # 3a. Every required status check is bound to a specific reporting App
  #     (OMN-19511). `required_status_checks.checks[].app_id` names the App
  #     GitHub will accept a check run with that context FROM; a null app_id
  #     ("Any source") means ANY App or Action with `checks:write` on the repo
  #     can satisfy the context, including one with no relation to the intended
  #     CI producer. That is a spoofable required check, not an enforced one --
  #     the same class of gap as an unbound `contexts[]` entry, just newer
  #     surface (GitHub added per-check app binding after `contexts[]` was
  #     deprecated). `.contexts[]` carries no app_id at all and is not judged
  #     here; `.checks[]` is the field that can express and therefore leak this.
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  local unbound_contexts
  unbound_contexts=$(printf '%s' "$protection" | jq -r '
    (.required_status_checks.checks // [])
    | map(select(.app_id == null) | .context)
    | join(", ")
  ')
  if [[ -z "$unbound_contexts" ]]; then
    echo "    [${branch}] PASS: every required status check is bound to a reporting App"
    emit_jsonl "$repo" "$branch" "required_check_app_binding" "PASS" "all bound"
  else
    echo "    [${branch}] FAIL: required status check(s) with app_id=null (\"Any source\" -- satisfiable by any App, not just the intended CI producer): ${unbound_contexts}"
    emit_jsonl "$repo" "$branch" "required_check_app_binding" "FAIL" "unbound contexts: ${unbound_contexts}"
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

  # 5. "Lane Identity Gate" — asserted on DEV only (OMN-18288).
  #
  # ASSERTED, not noted. The Receipt Gate above is informational on dev because
  # its coverage across repos is inconsistent and a hard assertion would fail
  # honest repos. This context has exactly one producing repo and exactly one
  # branch it can report on, so there is nothing inconsistent to be tolerant
  # of: it is either required on omniclaude dev or the gate it names is
  # advisory, which is the state OMN-18288 exists to end.
  #
  # The expectation moves in the SAME change that flips live branch protection,
  # which is the criterion's own wording. The alternative ordering -- flip
  # first, update the guard later -- makes the Branch Protection Guard red on
  # every omni_home pull request in the interval, which is how the 2026-08-24
  # to 2026-09-13 nine-day red window happened (see the release-synced-main
  # block above and rule 12's "verified: 2026-09-13" paragraph).
  if [[ "$branch" == "dev" ]] && requires_lane_identity_gate "$repo"; then
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    local lane_identity_gate
    lane_identity_gate=$(printf '%s' "$protection" | jq -r '
      (
        (.required_status_checks.contexts // [])
        + ((.required_status_checks.checks // []) | map(.context))
      )
      | map(select(. == "Lane Identity Gate"))
      | length
    ')
    if [[ "$lane_identity_gate" -ge 1 ]]; then
      echo "    [${branch}] PASS: \"Lane Identity Gate\" is a required status check"
      emit_jsonl "$repo" "$branch" "required_check_lane_identity_gate" "PASS" "Lane Identity Gate required"
    else
      echo "    [${branch}] FAIL: \"Lane Identity Gate\" not found in required status checks — the gate reports but cannot block (OMN-18288)"
      emit_jsonl "$repo" "$branch" "required_check_lane_identity_gate" "FAIL" "Lane Identity Gate missing"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    fi
  fi
}

# merge_queue_bypass_verdict <ruleset-detail-json>   (OMN-19929, check 6a)
#
# Pure: takes one ruleset DETAIL document as its only argument and prints
# exactly one verdict line:
#   NOT_QUEUE          -- disabled, or carries no merge_queue rule
#   NO_BYPASS          -- enforced merge-queue ruleset, bypass list read and empty
#   BYPASS <actors>    -- enforced merge-queue ruleset with bypass actor(s),
#                         each as <actor_type>:<actor_id>:<bypass_mode>
#   UNREADABLE         -- not a ruleset document, no rules, or no bypass_actors
#                         key (the token cannot read the list)
merge_queue_bypass_verdict() {
  local verdict
  verdict=$(printf '%s' "$1" | jq -r '
    if (type != "object") or (has("rules") | not) then "UNREADABLE"
    elif (.enforcement // "") == "disabled" then "NOT_QUEUE"
    elif ([.rules[]? | select(.type == "merge_queue")] | length) == 0 then "NOT_QUEUE"
    elif (has("bypass_actors") | not) or (.bypass_actors == null) then "UNREADABLE"
    elif (.bypass_actors | length) == 0 then "NO_BYPASS"
    else "BYPASS " + ([.bypass_actors[] | "\(.actor_type // "-"):\(.actor_id // "-"):\(.bypass_mode // "-")"] | join(","))
    end' 2>/dev/null) || verdict=""
  printf '%s\n' "${verdict:-UNREADABLE}"
}

# check_merge_queue_bypass <repo> <rulesets-list-json>   (OMN-19929, check 6a)
# The list endpoint carries neither rules nor bypass_actors, so every ruleset
# that is not disabled is re-read by id and judged by merge_queue_bypass_verdict.
check_merge_queue_bypass() {
  local repo="$1"
  local rulesets="$2"
  local full="${ORG}/${repo}"
  local ids rid detail verdict judged=0

  ids=$(printf '%s' "$rulesets" | jq -r '.[] | select((.enforcement // "") != "disabled") | .id')
  for rid in $ids; do
    detail=$(gh api "repos/${full}/rulesets/${rid}" 2>/dev/null) || {
      echo "  [repo] FAIL: ruleset ${rid} is unreadable, so whether it lets anyone merge outside the queue is unknown"
      emit_jsonl "$repo" "-" "merge_queue_ruleset_no_bypass" "FAIL" "ruleset ${rid} detail not fetchable"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
      judged=1
      continue
    }
    verdict=$(merge_queue_bypass_verdict "$detail")
    case "$verdict" in
      NOT_QUEUE) ;;
      NO_BYPASS)
        echo "  [repo] PASS: merge-queue ruleset ${rid} has no bypass actor"
        emit_jsonl "$repo" "-" "merge_queue_ruleset_no_bypass" "PASS" "ruleset ${rid}"
        judged=1
        ;;
      BYPASS\ *)
        echo "  [repo] FAIL: merge-queue ruleset ${rid} has bypass actor(s) ${verdict#BYPASS } -- that identity can merge outside the queue"
        emit_jsonl "$repo" "-" "merge_queue_ruleset_no_bypass" "FAIL" "ruleset ${rid}; bypass ${verdict#BYPASS }"
        REPO_OK=false
        FAILURES=$((FAILURES + 1))
        judged=1
        ;;
      *)
        echo "  [repo] FAIL: the bypass list of ruleset ${rid} is unreadable (no bypass_actors in the response; the token cannot administer the repo), so the absence of a bypass cannot be claimed"
        emit_jsonl "$repo" "-" "merge_queue_ruleset_no_bypass" "FAIL" "ruleset ${rid} bypass list unreadable"
        REPO_OK=false
        FAILURES=$((FAILURES + 1))
        judged=1
        ;;
    esac
  done
  if [[ "$judged" -eq 0 ]]; then
    echo "  [repo] PASS: no enforced merge-queue ruleset (nothing to bypass)"
    emit_jsonl "$repo" "-" "merge_queue_ruleset_no_bypass" "PASS" "no enforced merge-queue ruleset"
  fi
}

# in_list <needle> [item...] -- exact-match membership, safe on an empty list.
in_list() {
  local needle="$1" item
  shift
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

# check_default_branch <repo> <repo-settings-json>   (check 7, OMN-16289)
#
# The default branch must be `dev`. A repo still defaulting to `main` passes
# only while it is in the shrink-only DEFAULT_BRANCH_PENDING_REPOS baseline,
# and a baseline entry whose repo already defaults to `dev` FAILS so the list
# cannot go stale. An unreadable default branch FAILS: absence is not `dev`.
check_default_branch() {
  local repo="$1"
  local settings="$2"
  local default_branch pending=false

  if in_list "$repo" ${DEFAULT_BRANCH_PENDING_REPOS[@]+"${DEFAULT_BRANCH_PENDING_REPOS[@]}"}; then
    pending=true
  fi
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  default_branch=$(printf '%s' "$settings" | jq -r '.default_branch // ""' 2>/dev/null) || default_branch=""

  if [[ -z "$default_branch" ]]; then
    echo "  [repo] FAIL: default branch is unreadable, so \`dev\` cannot be claimed (OMN-16289)"
    emit_jsonl "$repo" "-" "default_branch_is_dev" "FAIL" "default branch unreadable"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  elif [[ "$default_branch" == "dev" ]]; then
    if $pending; then
      echo "  [repo] FAIL: ${repo} defaults to dev but is still in DEFAULT_BRANCH_PENDING_REPOS -- delete the stale baseline entry (OMN-16289)"
      emit_jsonl "$repo" "-" "default_branch_is_dev" "FAIL" "stale pending baseline entry"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    else
      echo "  [repo] PASS: default branch is dev"
      emit_jsonl "$repo" "-" "default_branch_is_dev" "PASS" "dev"
    fi
  elif $pending; then
    echo "  [repo] NOTE: default branch is ${default_branch}; ${repo} is in the shrink-only DEFAULT_BRANCH_PENDING_REPOS baseline (OMN-16289)"
    emit_jsonl "$repo" "-" "default_branch_is_dev" "NOTE" "default ${default_branch}; pending baseline"
  else
    echo "  [repo] FAIL: default branch is ${default_branch}, not dev -- dev is the default branch of every code repository (OMN-16289)"
    emit_jsonl "$repo" "-" "default_branch_is_dev" "FAIL" "default ${default_branch}"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
  fi
}

# check_main_targeting_prs <repo> <repo-settings-json>   (check 8, OMN-16289)
#
# Only judged for a repo whose default branch is `dev`: an open PR on `main`
# there is either a sanctioned path (head under hotfix/ or promotion/), a
# baselined entry in MAIN_PR_PENDING, or a feature PR aimed at the wrong base.
# A MAIN_PR_PENDING entry for this repo whose PR is no longer open against
# `main` FAILS as stale. An unreadable PR list FAILS.
check_main_targeting_prs() {
  local repo="$1"
  local settings="$2"
  local full="${ORG}/${repo}"
  local default_branch prs line number head entry judged=0 seen=" "

  default_branch=$(printf '%s' "$settings" | jq -r '.default_branch // ""' 2>/dev/null) || default_branch=""
  if [[ "$default_branch" != "dev" ]]; then
    return
  fi
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  prs=$(gh api "repos/${full}/pulls?state=open&base=main&per_page=100" 2>&1) || {
    echo "  [repo] FAIL: open pull requests on main are unreadable, so \"no feature PR targets main\" cannot be claimed"
    emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "FAIL" "pull request list not fetchable"
    REPO_OK=false
    FAILURES=$((FAILURES + 1))
    return
  }
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    number="${line%% *}"
    head="${line#* }"
    seen="${seen}${number} "
    if [[ "$head" =~ $SANCTIONED_MAIN_HEAD_REGEX ]]; then
      echo "  [repo] NOTE: ${repo}#${number} targets main from ${head} (sanctioned hotfix/promotion path)"
      emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "NOTE" "${repo}#${number} sanctioned ${head}"
    elif in_list "${repo}#${number}" ${MAIN_PR_PENDING[@]+"${MAIN_PR_PENDING[@]}"}; then
      echo "  [repo] NOTE: ${repo}#${number} targets main and is in the shrink-only MAIN_PR_PENDING baseline"
      emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "NOTE" "${repo}#${number} pending baseline"
    else
      echo "  [repo] FAIL: ${repo}#${number} (${head}) targets main; dev is the base branch -- gh pr edit ${number} --repo ${full} --base dev (OMN-16289)"
      emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "FAIL" "${repo}#${number} ${head}"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
    fi
    judged=1
  done < <(printf '%s' "$prs" | jq -r '.[] | "\(.number) \(.head.ref)"')

  for entry in ${MAIN_PR_PENDING[@]+"${MAIN_PR_PENDING[@]}"}; do
    if [[ "${entry%%#*}" == "$repo" && "$seen" != *" ${entry##*#} "* ]]; then
      echo "  [repo] FAIL: ${entry} is in MAIN_PR_PENDING but is not open against main -- delete the stale baseline entry (OMN-16289)"
      emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "FAIL" "stale baseline entry ${entry}"
      REPO_OK=false
      FAILURES=$((FAILURES + 1))
      judged=1
    fi
  done
  if [[ "$judged" -eq 0 ]]; then
    echo "  [repo] PASS: no open pull request targets main"
    emit_jsonl "$repo" "-" "no_feature_pr_targets_main" "PASS" "none open"
  fi
}

check_default_only_repo() {
  local repo="$1"
  local full="${ORG}/${repo}"
  local settings
  REPO_OK=true

  echo "───────────────────────────────────────"
  echo "Repo: ${full} (default-branch checks only)"
  echo "───────────────────────────────────────"
  settings=$(gh api "repos/${full}" 2>&1) || settings=""
  check_default_branch "$repo" "$settings"
  check_main_targeting_prs "$repo" "$settings"
  if $REPO_OK; then
    echo "  >>> COMPLIANT"
  else
    echo "  >>> NON-COMPLIANT"
  fi
  echo ""
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

  # 7 + 8. Default branch is dev; no feature PR targets main (OMN-16289)
  check_default_branch "$repo" "$repo_settings"
  check_main_targeting_prs "$repo" "$repo_settings"

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
      # 6a. No bypass actor on an enforced merge-queue ruleset (OMN-19929)
      TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
      check_merge_queue_bypass "$repo" "$rulesets"
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

for repo in ${DEFAULT_BRANCH_ONLY_REPOS[@]+"${DEFAULT_BRANCH_ONLY_REPOS[@]}"}; do
  check_default_only_repo "$repo"
done

echo "======================================="
echo " Summary: ${FAILURES} failure(s) across ${TOTAL_CHECKS} checks"
echo "======================================="

if [[ "$FAILURES" -gt 0 ]]; then
  exit 1
fi

echo "All repos compliant."
exit 0
