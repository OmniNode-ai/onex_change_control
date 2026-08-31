#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
#
# validate_canonical_emit_coverage.sh — Guard against canonical handler emit regressions.
#
# Verifies that each canonical handler in omnidash event-consumer.ts emits its
# expected granular bridge event. Catches the OMN-5132 class of bug where a
# canonical handler processes data but fails to emit the granular event that
# wires it into the projection bridge.
#
# Usage:
#   bash scripts/validation/validate_canonical_emit_coverage.sh [omnidash_root]
#
# Exit 0 = PASS, Exit 1 = FAIL, Exit 0 with WARN = structure changed
#
# OMN-5160
set -euo pipefail

OMNIDASH_ROOT="${1:-omnidash}"
EVENT_CONSUMER="${OMNIDASH_ROOT}/server/event-consumer.ts"

if [[ ! -f "$EVENT_CONSUMER" ]]; then
  echo "SKIP: $EVENT_CONSUMER not found"
  exit 0
fi

# Expected mapping: handler_name:expected_emit_event
HANDLERS=(
  "handleCanonicalNodeIntrospection:nodeIntrospectionUpdate"
  "handleCanonicalNodeHeartbeat:nodeHeartbeatUpdate"
  "handleCanonicalNodeBecameActive:nodeBecameActive"
)

failures=0
checked=0

for mapping in "${HANDLERS[@]}"; do
  handler="${mapping%%:*}"
  expected_emit="${mapping##*:}"
  checked=$((checked + 1))

  # Check if handler exists
  if ! grep -q "private ${handler}" "$EVENT_CONSUMER"; then
    echo "WARN: handler '${handler}' not found in $EVENT_CONSUMER — has the pattern changed?"
    continue
  fi

  # Extract the handler method body using awk brace-counting.
  # Starts at the handler declaration line, tracks { and } depth,
  # and stops when depth returns to 0 (method close).
  handler_body=$(awk "
    /private ${handler}/ { found=1; depth=0 }
    found {
      for (i=1; i<=length(\$0); i++) {
        c = substr(\$0,i,1)
        if (c==\"{\") depth++
        if (c==\"}\") depth--
      }
      print
      if (found && depth<=0 && NR>1) { exit }
    }
  " "$EVENT_CONSUMER")

  if echo "$handler_body" | grep -q "this\.emit('${expected_emit}'"; then
    echo "  PASS: ${handler} emits '${expected_emit}'"
  else
    echo "  FAIL: ${handler} does NOT emit '${expected_emit}'"
    failures=$((failures + 1))
  fi
done

echo ""
echo "Checked: ${checked} handlers"

if [[ $failures -gt 0 ]]; then
  echo "FAIL: ${failures} canonical handler(s) missing expected granular emit"
  exit 1
fi

echo "PASS: All ${checked} canonical handlers emit expected bridge events"
