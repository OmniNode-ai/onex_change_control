# OMN-15232 — falsifiable content-probe evidence (supplement to the autobind contract)

- **Ticket:** OMN-15232 — event_bus_kafka deserialization-path DLQ publish is UNGATED
- **Product PR:** OmniNode-ai/omnibase_infra#2497, merged as `6e91834b7464ab62ac49da3986e2365e45850fd3`
- **Pre-fix parent:** `f7fb7cdeba293003bfcb2e5eb92d8ac8acc1665b`
- **Probes executed:** 2026-07-27T14:27Z, read-only, against the GitHub contents API
- **Verifier:** `fable-plan-0727-omn15232-evidence` (independent of the implementer; did not author the fix)
- **Related:** OMN-15247 (autobind hollow-receipt mechanism gap), OMN-15227, OMN-13317

## Why this document exists instead of receipts

The OCC evidence companion that landed for OMN-15232 is `contracts/OMN-15232.yaml`, authored by the
Evidence-Source autobind producer. Its three `dod_evidence` checks are PR-existence probes:

```
gh pr view ${PR_NUMBER} --repo ${REPO} --json number,state
gh pr view ${PR_NUMBER} --repo ${REPO} --json files
gh pr view ${PR_NUMBER} --repo ${REPO} --json number,state
```

Each exits 0 for any PR that exists, in any state, carrying any diff. None can go RED given the PR
exists, so none of them distinguishes a correct fix from a revert, a README edit, or an empty PR.
The receipts minted from them are PASS by construction.

A hand-authored companion (OCC#5115, branch `jonah/omn-15232-occ`, head `1d604ccb5`) carried
falsifiable content probes for the same ticket, but autobind opened OCC#5118 for the same ticket and
that shape is the one that landed. This document re-lands the discarded evidence.

**It is a narrative evidence document, not a receipt, and that is a forced choice.**
`scripts/validation/check_receipt_hardening.py` rejects any post-cutoff receipt whose
`evidence_item_id` is absent from the ticket contract:

> `dod_evidence item <id> not found in <contract>; contract_entry_sha256 cannot be validated.`

The stronger evidence ids exist only in the displaced hand-authored contract, not in the merged
autobind one. Re-landing them as receipts would require modifying the already-merged
`contracts/OMN-15232.yaml`, which the append-only OCC discipline forbids. The mechanism gap this
exposes — displaced evidence is not recoverable through the receipt surface — is filed as OMN-15247.

## Probe records

Every probe below was executed read-only at the timestamp above. Exit codes and stdout are as
returned; nothing here is reconstructed or asserted from memory.

### P1 — deserialization path binds the DLQ result instead of discarding it

```
gh api "repos/OmniNode-ai/omnibase_infra/contents/src/omnibase_infra/event_bus/event_bus_kafka.py?ref=<REF>" \
  --jq .content | base64 -d | grep -c 'dlq_result = await self._publish_raw_to_dlq('
```

| REF | stdout | exit | verdict |
|---|---|---|---|
| `6e91834b` (merged fix) | `1` | 0 | GREEN |
| `f7fb7cde` (pre-fix parent) | `0` | 1 | RED |

Before the fix the call site was a bare `await self._publish_raw_to_dlq(` with the return value
discarded, so this probe is falsifiable by construction against the parent.

### P2 — `MixinKafkaDlq._publish_to_dlq` surfaces a real persistence outcome

```
gh api "repos/OmniNode-ai/omnibase_infra/contents/src/omnibase_infra/event_bus/mixin_kafka_dlq.py?ref=<REF>" \
  --jq .content | base64 -d | grep -c 'OMN-15232: surface the real persistence outcome'
```

| REF | stdout | exit | verdict |
|---|---|---|---|
| `6e91834b` (merged fix) | `1` | 0 | GREEN |
| `f7fb7cde` (pre-fix parent) | `0` | 1 | RED |

This is the second ungated site found by the required module audit. Before the fix
`_publish_to_dlq` returned `None` unconditionally, so no caller could have gated on it.

### P3 — RED-first regression test with the static call-site ratchet exists

```
gh api "repos/OmniNode-ai/omnibase_infra/contents/tests/unit/event_bus/test_deser_dlq_fail_closed_omn15232.py?ref=<REF>" \
  --jq .content | base64 -d | grep -c 'def test_no_dlq_publish_call_site_discards_its_persistence_result'
```

| REF | stdout | exit | verdict |
|---|---|---|---|
| `6e91834b` (merged fix) | `1` | 0 | GREEN |
| `f7fb7cde` (pre-fix parent) | file absent (HTTP 404) | 1 | RED |

The ratchet asserts that no DLQ-publish call site in `event_bus_kafka.py` discards its persistence
result, so a future regression at either site fails this test rather than silently reopening the
defect.

## What this establishes

All three probes form a real RED/GREEN pair across the merge boundary: each returns non-zero at the
merge-base and zero at the merge commit. That is the property the landed autobind checks lack
entirely, and it is the property that makes the OMN-15232 Done flip (2026-07-27T14:11:06Z)
defensible. The Done flip itself rode the hollow autobind receipt; this document supplies the
evidence it should have rested on.

Scope note: these are static content probes against merged source. They prove the gating code and
its regression test are present at the merged SHA. They are not runtime proof that a DLQ write
failure now rewinds the fetch position in a live lane; that would require a lane-level fault
injection, which was not run and is not claimed here.
