OMN-18783

Quoted cause: "Eight onex_change_control checks appear in none of `required_status_checks`, `EXPECTED_EXTERNAL_CONTEXTS`, `STRICT_GATE_JOBS` or `SKIPPABLE_GATE_JOBS`:"

Readback partly falsified that premise: the expiry gate was already strict. The remaining gates were advisory, exempt, absent from the enforcing tuples, or observed only when present. This branch integrates the prior implementation and wires all eight named gates into CI Summary, which is required on dev.

The existing producers now propagate URL/environment validator failures, distinguish both self-companion checks, and report hygiene on every governed PR base. PEP 604 evidence-only changes skip steps inside a successful check. The pinned hygiene validator enforces added lines; allowed roots, suppressions and baselines do not grow. Regression controls run in existing CI Summary and pre-commit. Existing CI producers and `scripts/ci/ci_summary_gate.py` handle the change; no new capability or script was added.

Validation:
- Focused RED before wiring: 16 failed, 22 passed. Final GREEN: 40 passed.
- Privileged-author fixtures use the CI command's API adapter: Bot succeeds; User changing source is refused; each result reaches the umbrella.
- Changed-file pre-commit and focused Ruff checks passed.
- The pinned hygiene validator refused a temporary private-network literal (exit 1) and accepted the restored diff (exit 0). Its stale private-repository visibility snapshot was refreshed in scratch from the live organization API; canonical vocabulary was unchanged.

Lab: host=h201 lane=lab-fill-omn18783-10100910 command=uv run --frozen pytest tests/ci/test_ci_summary_gate.py -q -k OccGateEnforcementOmn18783 observed=40 passed.

Live default-branch sources and required contexts were reconciled read-only for the requested sibling repositories. Their unclassified producers remain review candidates needing individual adjudication; no sibling repository was changed. Post-merge live umbrella readback and this PR's CI remain pending. Grouped B19 scope and wave exits remain gated; the ticket state is unchanged.

Controls use the existing CI Summary job. Additional runner time and PR wall time have not been measured.
