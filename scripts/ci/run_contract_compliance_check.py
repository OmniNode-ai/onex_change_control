#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Thin CI wrapper -- see onex_change_control.scripts.contract_compliance_check.

OMN-14458: the check-execution engine (check runners, inert-check detection,
demotion rules, contract loading, run_compliance_check orchestration) moved
to ``onex_change_control.scripts.contract_compliance_check`` -- the installed
package -- so downstream repos and local preflight tooling can import the
exact same code instead of forking it. This file re-exports the full module
surface and is kept at this exact path because other repos' CI workflows
invoke it directly:

    python scripts/ci/run_contract_compliance_check.py \\
        --pr 123 --repo OmniNode-ai/omnimarket --workspace <product-checkout>

Do not add logic here. Any behavior change belongs in the package module.
"""

from __future__ import annotations

import sys

from onex_change_control.scripts.contract_compliance_check import (
    _ALLOWLIST_FIELDS,
    _CHECK_RUNNERS,
    _INERT_CHECK_PATTERNS,
    _OMN_TICKET_PATTERN,
    _REPO_PATTERN,
    _RESULT_BLOCK,
    _RESULT_PASS,
    _RESULT_WARN,
    _build_command_env,
    _check_command,
    _check_endpoint,
    _check_file_exists,
    _check_grep,
    _check_test_exists,
    _check_test_passes,
    _CheckContext,
    _contract_digest,
    _demote,
    _extract_ticket_id,
    _find_contracts_dir,
    _has_effective_check,
    _is_inert_check,
    _load_legacy_allowlist,
    _load_yaml,
    _maybe_demote_precommit,
    _run,
    _run_dod_checks,
    _run_single_check,
    _substitute_tokens,
    _superseded_dod_ids,
    _supersedes_marker,
    main,
    run_compliance_check,
)

__all__ = [
    "_ALLOWLIST_FIELDS",
    "_CHECK_RUNNERS",
    "_INERT_CHECK_PATTERNS",
    "_OMN_TICKET_PATTERN",
    "_REPO_PATTERN",
    "_RESULT_BLOCK",
    "_RESULT_PASS",
    "_RESULT_WARN",
    "_CheckContext",
    "_build_command_env",
    "_check_command",
    "_check_endpoint",
    "_check_file_exists",
    "_check_grep",
    "_check_test_exists",
    "_check_test_passes",
    "_contract_digest",
    "_demote",
    "_extract_ticket_id",
    "_find_contracts_dir",
    "_has_effective_check",
    "_is_inert_check",
    "_load_legacy_allowlist",
    "_load_yaml",
    "_maybe_demote_precommit",
    "_run",
    "_run_dod_checks",
    "_run_single_check",
    "_substitute_tokens",
    "_superseded_dod_ids",
    "_supersedes_marker",
    "main",
    "run_compliance_check",
]

if __name__ == "__main__":
    sys.exit(main())
