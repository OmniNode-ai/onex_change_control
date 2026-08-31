#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

# ONEX TypeScript/JavaScript Stub Detector
#
# Detects forbidden stub markers in TS/JS files using grep.
#
# Forbidden: // stub: (any case variation)
# Allowed:   // stub-ok: <reason>, // intentional-skip: <reason>
#
# Usage:
#   check_ts_stubs.sh <file1.ts> [file2.tsx] [file3.js] ...
#   check_ts_stubs.sh  (reads filenames from stdin when used as pre-commit hook)

set -euo pipefail

# Collect files: from args or stdin (pre-commit passes filenames as args)
files=()
if [[ $# -gt 0 ]]; then
    files=("$@")
else
    while IFS= read -r f; do
        files+=("$f")
    done
fi

# Filter to TS/JS files only, skip node_modules and dist
filtered=()
for f in "${files[@]}"; do
    case "$f" in
        */node_modules/*|*/dist/*|*/.next/*) continue ;;
    esac
    case "$f" in
        *.ts|*.tsx|*.js|*.jsx|*.mts|*.mjs|*.cts|*.cjs) filtered+=("$f") ;;
    esac
done

if [[ ${#filtered[@]} -eq 0 ]]; then
    exit 0
fi

found=0

for f in "${filtered[@]}"; do
    [[ -f "$f" ]] || continue

    # Find lines with "// stub:" (case-insensitive) but NOT "// stub-ok:" or "// intentional-skip:"
    # Strategy: grep for stub:, then exclude allowed markers
    while IFS= read -r match; do
        line_no="${match%%:*}"
        line_content="${match#*:}"

        # Check if this is actually an allowed marker (stub-ok or intentional-skip)
        # Case-insensitive check
        lower_line=$(echo "$line_content" | tr '[:upper:]' '[:lower:]')

        if echo "$lower_line" | grep -qE '//\s*stub-ok\s*:'; then
            continue
        fi
        if echo "$lower_line" | grep -qE '//\s*intentional-skip\s*:'; then
            continue
        fi

        echo "STUB DETECTED: $f:$line_no: $line_content"
        found=1
    done < <(grep -inE '//\s*stub\s*:' "$f" | head -100 || true)
done

if [[ $found -ne 0 ]]; then
    echo ""
    echo "ERROR: Forbidden stub markers found in TypeScript/JavaScript files."
    echo ""
    echo "To fix:"
    echo "  - Implement the stubbed functionality, OR"
    echo "  - Replace '// stub:' with '// stub-ok: <reason>' if intentional, OR"
    echo "  - Replace '// stub:' with '// intentional-skip: <reason>' if deliberately skipped"
    echo ""
    exit 1
fi

exit 0
