# DoD Receipt Hashing, Append-Only, and Supersession (OMN-13888)

This document describes how DoD receipts bind to their contract, how existing
receipts are protected from rewrites, and how a receipt is corrected without
editing any merged file. It supersedes the whole-file-hash binding described in
earlier receipt-gate notes.

## 1. Per-entry contract hashing

A receipt binds to **one `dod_evidence` item**, not the whole contract file.
`omnibase_core.validation.validator_receipt_gate.compute_contract_entry_sha256`
computes a canonical hash over:

- an immutable header subset — `ticket_id` + `schema_version` only, and
- the parsed `dod_evidence` item itself (id + description + source + status +
  all `checks[]`).

The input is the parsed contract (`yaml.safe_load`) and the output is canonical
JSON (sorted keys, no whitespace), so a `yamlfmt` reflow / reindent / requote
that preserves parsed semantics yields an identical hash. Receipts record this
value in the `contract_entry_sha256` field.

**Why:** appending `dod_evidence` entry N+1 does not change the hash of entries
1..N, so prior receipts stay valid. This removes the OCC#3521 / OMN-13875
Nth-consumer lockout where appending one entry forced a rewrite of every prior
merged receipt.

## 2. Dual-accept transition (grandfathering)

Both OCC gates (`validator_occ_merge_eligibility` and the receipt gate) accept:

| Receipt binding | Rule |
|-----------------|------|
| `contract_entry_sha256` present | Strict — must equal the recomputed per-entry hash (a forged receipt fails). Takes precedence over `contract_sha256`. |
| Legacy `contract_sha256` only, bound to THIS PR | Whole-file check — must match the current contract file hash. |
| Legacy `contract_sha256` only, a PRIOR merged PR | Grandfathered — never re-hashed against the since-grown file. |

A receipt with **neither** binding is a hard fail after the OMN-10421 cutoff.

## 3. Append-only enforcement

`omnibase_core.validation.validator_occ_append_only` rejects, given the contract
at the merge base and at the PR head:

- editing an existing `dod_evidence` item (its per-entry hash changed), and
- removing an existing `dod_evidence` item.

Appending a brand-new item id is allowed; a net-new contract passes trivially.
Separately, any `M`/`D`/`R` git diff of an existing receipt file under
`drift/dod_receipts/<TICKET>/` is a violation — corrections are net-new
`.supersede.<NNNN>.yaml` add-only files.

> Wiring status: the append-only CI workflow + pre-commit hook are wired in a
> coordinated follow-up OCC PR **after** omnibase_core 0.46.5 (which ships the
> validator) is released and repinned, so the required check does not reference
> an unreleased module. Until then the validator is invokable but advisory.

## 4. Supersession / tombstones

A receipt key `<TICKET>/<EVIDENCE_ITEM>/<CHECK_TYPE>` may be corrected by
appending net-new records alongside the immutable base file:

```
drift/dod_receipts/<TICKET>/<EVIDENCE_ITEM>/<CHECK_TYPE>.yaml               # base (immutable)
drift/dod_receipts/<TICKET>/<EVIDENCE_ITEM>/<CHECK_TYPE>.supersede.<NNNN>.yaml   # append-only chain
```

The highest `NNNN` record is authoritative (`ModelReceiptSupersession`):

- **tombstone** (`tombstone: true`, no `replacement`) → the key has no active
  receipt (invalidation).
- **rebind** (`replacement:` receipt) → the key resolves to the replacement,
  which must key-match and carry a `contract_entry_sha256`.

A later record can un-tombstone a key by supplying a replacement at a higher
`NNNN`. The base file is never edited. Resolution is honored by:

- `validator_occ_merge_eligibility` and the receipt gate (via
  `validator_receipt_supersession.resolve_supersession`), and
- omnimarket `DurableEvidenceGate` (via `apply_supersessions`, ordered by
  `created_at` since payloads carry no filename), so a re-bound / invalidated PR
  citation no longer feeds the MERGED-PR check.

**Worked example — OMN-13899:** the base receipt
`dod-omniclaude-pr-1845/command.yaml` bound omniclaude PR #1845, which closed
unmerged. `command.supersede.0001.yaml` re-binds the key to the MERGED PR #1846
(merge commit `7318d03a…`). The base file is untouched; the durable-evidence
gate honors the rebind and Check 2 passes on #1846.

## 5. dod_verify dev-resolution rider

OCC governance is dev-first (contracts/receipts land on `dev`, batch to `main`
later), but the canonical clones track `main`. `EvidenceCollector` therefore
materialises an `origin/dev` worktree of the OCC repo when a contract is absent
from the working tree, and runs both the contract load and the receipt greps
inside it. `OCC_GOVERNANCE_REF` overrides the `origin/dev` default. This fixes
the OMN-13899 "No contract found" for dev-only contracts.

## References

- omnibase_core PR #1382 — per-entry hashing, dual-accept, supersession,
  append-only validator.
- omnimarket PR #1592 — dod_verify dev-resolution rider + tombstone honoring.
- OMN-13888 (design note, Option A), OMN-13875, OMN-13899.
