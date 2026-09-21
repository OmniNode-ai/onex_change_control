**Source: M3 closure plan, 2026-09-10, item 1.8 (chain 1)** — knowledge-base-internal `beta/plans/2026-09-10-m3-closure-plan.md`, PR #348.

> **Build the C6 pair —** `key-lifecycle-golden` **and** `key-lifecycle-error` (§4a.2) as legs of the same harness. **C6's proof is a subset of C5's walk** (S3 *is* the key lifecycle), so building it separately would duplicate the substrate.

**Owner (per plan):** a dispatched lane.

**Evidence the closer needs (per plan):** the board reading C6 from both legs' records.

**Golden/error chain pair it belongs to — §4a.2, C6, browser API-key create → list → revoke:**

|  | `key-lifecycle-golden` | `key-lifecycle-error` |
| -- | -- | -- |
| the chain | In one browser session: create a key, list it and see it, use it successfully, revoke it — with the API confirmation taken inside that same session | (a) the revoked key must stop authenticating and its backing row must be absent; (b) the stored hash must not authenticate; (c) a key belonging to tenant A must not authenticate as tenant B |
| where it runs | lab overlay first, then staging | same |
| what it emits | a lifecycle record: one check per step with the key id (never the value) as evidence | one record per refusal, plus `control` = the same key authenticating successfully before revocation, in the same run |
| what reads it | the board's C6 proof | the board's C6 proof |
| harness it extends | the same Playwright suite as §4a.1 — C6 is a leg of C5's walk, and S3 IS the key lifecycle | same |

**FACT worth carrying forward (per plan):** the error chain's assertions have already been observed once by hand, over a management channel, on 2026-09-09 — raw key 200, stored-hash 401, revoke 200, delete 1, absence 0. That is the shape to automate, and it is evidence the chain will pass, not evidence that it exists.

**Probe/run named in the plan:** extends the C5 Playwright suite (`omniweb` → `playwright.config.ts`, `e2e/billing.spec.ts`; `omnidash` → `playwright.config.ts`, `tests/e2e/`).

Closes on mechanical proof only (RULING docs/tracking/ROLLING_WORK_LEDGER.md 2026-09-10 :6247): a named probe/run green and read by the board or bar; recordings are supplementary.
