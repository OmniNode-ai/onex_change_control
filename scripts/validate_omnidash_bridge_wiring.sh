#!/usr/bin/env bash
# Convention guard: verifies all projection bridge listeners defined in
# omnidash server/index.ts have corresponding .on() wiring calls.
#
# Catches the OMN-5132 class of bug: listener defined but never connected.
# This is a text-convention validator, not a semantic parser.
set -euo pipefail

INDEX_FILE="${1:-omnidash/server/index.ts}"

if [[ ! -f "$INDEX_FILE" ]]; then
  echo "SKIP: $INDEX_FILE not found"
  exit 0
fi

# Extract listener names defined in projectionBridgeListeners object
# Pattern: "    listenerName: (event..." at the start of a property
defined=$(grep -E '^\s+\w+:\s*\(' "$INDEX_FILE" | sed 's/^[[:space:]]*//; s/:.*//' | sort)

# Extract listener names wired via .on() calls
wired=$(grep -oE "eventConsumer\.on\('[^']+'" "$INDEX_FILE" | sed "s/eventConsumer.on('//; s/'//" | sort)

if [[ -z "$defined" ]]; then
  echo "WARN: No bridge listeners found in projectionBridgeListeners — has the pattern changed?"
  exit 0
fi

# Find defined but not wired
unwired=$(comm -23 <(echo "$defined") <(echo "$wired"))

if [[ -n "$unwired" ]]; then
  echo "FAIL: Bridge listeners defined but not wired in $INDEX_FILE:"
  echo "$unwired" | while read -r name; do
    echo "  - $name (missing: eventConsumer.on('$name', projectionBridgeListeners.$name))"
  done
  exit 1
fi

echo "PASS: All $(echo "$defined" | wc -l | tr -d ' ') bridge listeners are wired"
