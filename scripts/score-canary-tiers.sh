#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TIER_FILE="$REPO_ROOT/.canary/tier-assignments.yaml"
OMNI_HOME="${OMNI_HOME:-$(cd "$REPO_ROOT/.." && pwd)}"

DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

if [[ ! -f "$TIER_FILE" ]]; then
  echo "ERROR: tier-assignments.yaml not found at $TIER_FILE" >&2
  exit 1
fi

parse_tiers() {
  local current_tier="" current_desc=""
  local in_repos=false
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name:[[:space:]]*(.+) ]]; then
      current_tier="${BASH_REMATCH[1]}"
      in_repos=false
    elif [[ "$line" =~ ^[[:space:]]*description:[[:space:]]*(.+) ]]; then
      current_desc="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ ^[[:space:]]*repos: ]]; then
      in_repos=true
    elif $in_repos && [[ "$line" =~ ^[[:space:]]*-[[:space:]]*(.+) ]]; then
      echo "${current_tier}|${BASH_REMATCH[1]}|${current_desc}"
    elif [[ -n "$line" && ! "$line" =~ ^[[:space:]]*# && ! "$line" =~ ^[[:space:]]*- && ! "$line" =~ ^version ]]; then
      in_repos=false
    fi
  done < "$TIER_FILE"
}

score_repo() {
  local repo_name="$1"
  local repo_path="$OMNI_HOME/$repo_name"
  local score=0

  if [[ ! -d "$repo_path/.git" ]]; then
    echo "0 (not found)"
    return
  fi

  local commit_count
  commit_count=$(git -C "$repo_path" log --oneline --since="30 days ago" 2>/dev/null | wc -l | tr -d ' ')
  score=$((score + commit_count))

  local dep_count=0
  if [[ -f "$repo_path/pyproject.toml" ]]; then
    dep_count=$(grep -c "omni" "$repo_path/pyproject.toml" 2>/dev/null || true)
  elif [[ -f "$repo_path/package.json" ]]; then
    dep_count=$(grep -c "omni" "$repo_path/package.json" 2>/dev/null || true)
  fi
  score=$((score + dep_count * 5))

  echo "$score"
}

echo "=== Canary Tier Assignments ==="
echo ""

current_tier=""
while IFS='|' read -r tier repo desc; do
  if [[ "$tier" != "$current_tier" ]]; then
    current_tier="$tier"
    echo "--- Tier: ${tier} ---"
    echo "    ${desc}"
    echo ""
  fi

  if $DRY_RUN; then
    echo "  ${repo}: (dry-run, scoring skipped)"
  else
    local_score=$(score_repo "$repo")
    echo "  ${repo}: score=${local_score}"
  fi
done < <(parse_tiers)

echo ""
echo "=== Done ==="
