## Summary
Ticket OMN-20157 has every acceptance criterion proven (falsifiers pass, a fresh lab receipt exists, independent acceptance merged in onex_change_control#13195 and #13316) but cannot pass dod_verify, because four old draft bindings stay unaccepted and dod_verify counts every unaccepted binding, including those on superseded items, with no way to retire one. Accepting bindings that prove nothing would weaken the gate, so they are retired instead, with the typed retirement that omnimarket#3570 teaches node_dod_verify to read.

## Changes
- `ModelAcBindingRetirement` (the `supersedes_ac_binding` entry) gains four optional fields: `reason_kind` (`superseded_by` or `no_longer_applicable`), `superseded_by`, `retired_by`, `retired_at`. They are optional so retirements already merged still parse; a half-written typed retirement is refused. New tests: `tests/test_omn_20157_typed_binding_retirement.py` (12 passed), beside `tests/test_omn_18577_static_evidence_on_live_criterion.py`.
- `contracts/OMN-20157.yaml` gains one `dod_evidence` item, `dod-omn20157-retire-unproven-draft-bindings`, with four retirements (`retired_by` dod-retire-binding-1620, `retired_at` 2026-10-08T16:48:51Z). It binds no criterion and accepts nothing; the retired items stay in the contract and keep running their own checks.

## Retirements
- AC5 from dod-omn20157-ac5-gemini-delegation-receipt (superseded_by dod-omn20157-accept-ac5-gemini-receipt-fec634b0: its check greps ledger text about lab run 39465f89, whose run directory is gone)
- AC5 from dod-omn20157-fill-ac5-gemini-run-f8a0b10d (superseded_by the same item: greps ledger text about lab run f8a0b10d, not re-read)
- AC5 from dod-omn20157-fill-ac5-plan-refusal-run-02b68882 (no_longer_applicable: a refusal run, BYOK_CODING_PLAN_NOT_PERMITTED, not a Gemini delegation; the GLM coding-plan leg was left out of the beta by ruling 2026-09-30T23:01:09Z)
- AC6 from dod-OmniNode-ai-omnimarket-pr-3310 (superseded_by dod-omn20157-accept-fill-ac6-typed-refusals: greps the class name ByokPinNotPermittedError)

Each reason is written out in the contract. The AC1 to AC5 bindings on pr-3310 are accepted and are not touched.

## Evidence
- Receipt: drift/dod_receipts/OMN-20157/dod-omn20157-retire-unproven-draft-bindings/test_passes.yaml, captured with check_receipt_hardening.py --capture-probe on lab host h202: the retirement tests from omnimarket#3570 at omnimarket 65d5e9ad1539, 51 passed.
- Verdict before: dod_verify for OMN-20157 reported AC_BINDING_SELF_ACCEPTED naming exactly the four bindings (acceptance_self_accepted_bindings 4).
- Verdict with omnimarket#3570 code and this contract: acceptance_self_accepted_bindings 0, acceptance_retired_bindings 4, acceptance_refused_retirements 0; the only failing check was this item's own test, because tests/unit/nodes/node_dod_verify/test_ac_binding_retirement.py is not yet on the canonical omnimarket clone.

## Order of landing
omnimarket#3570 first, then this PR. Landing this first leaves the old verifier ignoring the retirements and this item's check failing until #3570 is on the canonical omnimarket clone.
## Lab
Lab: h202, lane dod-retire-binding-1620, check_receipt_hardening.py --capture-probe of the omnimarket#3570 retirement tests at omnimarket 65d5e9ad1539: 51 passed.

Reopened as the onexbot-occ-writer App; replaces onex_change_control#13323, closed because check-human-authored-privileged-pr refuses a human-authored PR on src/.
