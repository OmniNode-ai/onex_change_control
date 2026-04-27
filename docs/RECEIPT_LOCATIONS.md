# DoD Receipt Locations — Reconciliation & Migration

> **Ticket:** OMN-9791 (Wave C, Task 11)
> **Hard cutoff:** **2026-06-01** (enforced in code, not just docs)
> **Status of legacy shape:** deprecated 2026-04-26, rejected on or after 2026-06-01

---

## Why this document exists

Before OMN-9791, ONEX had **two** DoD-receipt locations on the platform:

1. **`onex_change_control`** wrote per-item, per-run receipts under
   `drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml`.
2. The DoD compliance gate (`scripts/check_dod_compliance.py`) only looked at
   the legacy roll-up at `.evidence/<TICKET>/dod_report.json` produced by the
   old verifier path.

Two locations means two truths. The gate could be fooled by a stale, hand-
crafted, or partially-true `dod_report.json` even when the canonical
per-receipt corpus disagreed. This document records the canonical decision,
the deprecation timeline, and the migration path.

---

## Canonical receipt location

```
drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml
```

* Schema: `omnibase_core.ModelDodReceipt` (one file per probe run).
* Granularity matches the `dod_evidence` items declared in the ticket
  contract — one canonical receipt directory per `dod_evidence` item.
* YAML aligns with `contracts/<TICKET>.yaml` so a reader does not need to
  swap formats mid-flow.
* Aggregation (PASS / FAIL roll-up) is computed by the gate from the per-
  receipt files. It is **not** a stored field in the receipt itself.

This is the only receipt shape the gate accepts on or after **2026-06-01**.

---

## Legacy receipt location (deprecated)

```
.evidence/<TICKET>/dod_report.json
```

* Single roll-up JSON of the form `{"result": {"failed": <int>, ...}, ...}`.
* Opaque: hides per-item evidence behind a single `failed == 0` boolean.
  This is exactly the opacity that lets a contributor-fabricated or stale
  receipt appear clean while the canonical per-item evidence disagrees.
* Deprecated **2026-04-26** (Wave C of the session-process plan).
* **Rejected on or after 2026-06-01** by `check_receipt_exists` in both
  `scripts/check_dod_compliance.py` and
  `src/onex_change_control/handlers/handler_dod_sweep.py`.

---

## Reconciliation behaviour (during the transition window)

The two reconciliation entrypoints share a single `_LEGACY_RECEIPT_CUTOFF =
date(2026, 6, 1)` constant. Both accept an injected `now: datetime` so tests
and tooling can advance the clock deterministically; production code passes
`None` and the function resolves the wall-clock at call time (we never use
`datetime.now()` as a parameter default per the omnibase_core handshake).

| Canonical present | Legacy present | `now` vs cutoff       | Result                                                                   |
|-------------------|----------------|-----------------------|--------------------------------------------------------------------------|
| yes               | any            | any                   | `PASS` — canonical wins; no warning.                                     |
| no                | yes            | `now < 2026-06-01`    | `PASS` — detail string contains `DEPRECATED`; emits `DeprecationWarning`. |
| no                | yes            | `now >= 2026-06-01`   | `FAIL` — detail string references OMN-9791 and the cutoff date.          |
| no                | no             | any                   | `FAIL` — `no receipt at <canonical> or <legacy>`.                        |

The `DeprecationWarning` references **OMN-9791** so log scrapers and CI
artifact processors can correlate the signal back to this ticket.

The `is_dir() + rglob("*.yaml")` check means an empty or YAML-less canonical
directory does not satisfy "canonical present" — only an actual receipt
file does.

---

## Migration path

For each ticket whose only receipt is the legacy `dod_report.json`:

1. Run the per-item probe again (or re-attach existing per-item evidence) so
   the run produces a `ModelDodReceipt` per `dod_evidence` item.
2. Write each receipt to
   `drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml`.
3. Optionally delete `.evidence/<TICKET>/dod_report.json` once the canonical
   corpus is in place (the gate ignores it as soon as canonical is present).

Wave C, Task 10 owns the bulk-migration script for historical receipts. This
document describes the gate-side reconciliation contract; Task 10 owns the
data move.

---

## Where this is enforced

* `scripts/check_dod_compliance.py::check_receipt_exists` — direct CLI path
  used by `pre-commit` and CI markdown summary mode.
* `src/onex_change_control/handlers/handler_dod_sweep.py::check_receipt_exists`
  — structured `ModelDodSweepResult` path used by `--json` mode and other
  programmatic consumers.

Both share the `_LEGACY_RECEIPT_CUTOFF` constant (`date(2026, 6, 1)`) and
the same logic. The handler-test
`test_handler_cutoff_constant_matches_script` asserts the constants stay in
lock-step; if you change one, you change both, or CI breaks.

---

## Acceptance trace (OMN-9791)

* `RECEIPT_LOCATIONS.md` exists with explicit deprecation date 2026-06-01 — this file.
* Gate prefers canonical; falls back to legacy with PASS-and-warning detail
  containing `DEPRECATED` — covered by
  `tests/unit/scripts/test_check_dod_compliance.py::TestTwoReceiptLocationReconciliation::test_legacy_only_passes_with_deprecated_warning_pre_cutoff`
  and the matching handler test.
* Gate fails closed when only the legacy location is present on or after
  2026-06-01 — covered by
  `test_legacy_only_fails_on_cutoff` and `test_legacy_only_fails_post_cutoff`.
* Gate fails if neither location is present — covered by
  `test_neither_present_fails`.
* Existing behaviour (canonical present, both present, contract checks,
  exemption flow) preserved — covered by the rest of the suite, all green
  after the change.
