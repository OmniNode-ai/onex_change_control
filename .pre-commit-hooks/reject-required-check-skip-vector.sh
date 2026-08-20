#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
#
# OMN-15241 (fan-out of the OMN-14854 omniclaude canary via the OMN-14865
# omnibase_infra pattern): Required-Check Skip-Vector Guard -- local
# (pre-commit) run.
#
# WHY THIS EXISTS: this repo already calls the guard in CI
# (.github/workflows/required-check-skip-guard-caller.yml) but had no local
# hook, so a skip-vector violation committed with every local hook GREEN and
# was caught CI-only -- the detection-without-local-enforcement class root
# CLAUDE.md Rule #5 exists to kill.
#
# DRY, NOT a re-implementation: omniclaude is the canonical source for
# validate_no_required_check_skip_vectors.py (its own reusable workflow says
# so: all repos call it via `uses:` instead of running their own copy). This
# wrapper executes that SAME file, resolved from the OMNI_HOME sibling clone
# -- identical to omnibase_infra's copy of this hook. Local and CI verdicts
# cannot diverge on validator logic, because both run the identical script
# against this repo's own .github/required-checks.yaml manifest and
# .github/workflows/ directory.
#
# FAIL-LOUD (root CLAUDE.md Rule #8, and the fail-loud pre-commit meta-gate):
# if OMNI_HOME is unset or the sibling omniclaude clone does not contain the
# validator, this hook hard-errors (exit 1) with remediation. It never
# degrades to a green skip -- a gate that cannot run must be
# byte-indistinguishable from a failing gate.
#
# Honest drift note: CI resolves the validator at the omniclaude ref pinned in
# required-check-skip-guard-caller.yml; this local hook runs whatever is
# currently checked out in $OMNI_HOME/omniclaude (tracking dev). A same-session
# `git -C "$OMNI_HOME/omniclaude" pull --ff-only` keeps them converged.

set -euo pipefail

if REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" && [[ -n "$REPO_ROOT" ]]; then
    :
else
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ -z "${OMNI_HOME:-}" ]]; then
    echo "ERROR: OMNI_HOME is not set. required-check-skip-guard needs the" >&2
    echo "canonical validator from the omniclaude sibling clone at" >&2
    # Deliberately single-quoted/literal, not an expansion.
    # shellcheck disable=SC2016
    echo '$OMNI_HOME/omniclaude -- set OMNI_HOME to the omni_home path.' >&2
    exit 1
fi

VALIDATOR="${OMNI_HOME}/omniclaude/.github/actions/required-check-skip-guard/validate_no_required_check_skip_vectors.py"

if [[ ! -f "${VALIDATOR}" ]]; then
    echo "ERROR: canonical validator missing at ${VALIDATOR}." >&2
    echo "Ensure \$OMNI_HOME/omniclaude is a checked-out clone containing" >&2
    echo ".github/actions/required-check-skip-guard/validate_no_required_check_skip_vectors.py" >&2
    echo "(git -C \"\$OMNI_HOME/omniclaude\" pull --ff-only)." >&2
    exit 1
fi

# Interpreter selection. The validator needs PyYAML. A bare `python3` from PATH
# is NOT guaranteed to have it (a uv-managed shim on PATH commonly does not),
# and a hook that hard-errors on every commit for a missing third-party import
# is a hook developers rip out. Order: explicit override -> a python3 that can
# actually import yaml -> uv's ephemeral env. No silent degrade at any step:
# if none of the three resolve, this exits 1.
run_validator() {
    "$@" "${VALIDATOR}" \
        --manifest "${REPO_ROOT}/.github/required-checks.yaml" \
        --workflows-dir "${REPO_ROOT}/.github/workflows"
}

if [[ -n "${PYTHON_BIN:-}" ]]; then
    exec_cmd=("${PYTHON_BIN}")
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
    exec_cmd=(python3)
elif command -v uv >/dev/null 2>&1; then
    exec_cmd=(uv run --no-project --with pyyaml python)
else
    echo "ERROR: required-check-skip-guard cannot resolve a Python with PyYAML." >&2
    echo "Install PyYAML for python3, install uv, or set PYTHON_BIN to an" >&2
    echo "interpreter that can import yaml. This gate does not degrade." >&2
    exit 1
fi

run_validator "${exec_cmd[@]}"
