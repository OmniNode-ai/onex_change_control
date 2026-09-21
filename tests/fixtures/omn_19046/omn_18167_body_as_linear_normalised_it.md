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

<!-- onex:dod-acceptance-criteria:begin -->

## Acceptance criteria

*Serialized from this ticket's* `onex_change_control` *contract (*`requirements[].acceptance[]`*) by* `onex-serialize-ac-section` *(OMN-18270). Edit the contract, not this section — the next run overwrites it.*

* **AC1** — The C6 golden leg runs on the dev plane and emits a `key-lifecycle-golden` record declaring criterion C6 with result PASS. The onex-lab overlay deliberately omits omniweb and Keycloak, so under rule 24(d) the dev plane is the applicable plane here and no lab pass is claimed. -- falsifier: `key-lifecycle-golden.json` from the named run satisfies `.chain == "key-lifecycle-golden" and .kind == "golden" and .criterion == "C6" and .target.plane == "dev" and .result == "PASS"`.
* **AC2** — The golden leg's five lifecycle steps all pass inside one browser session: the key surface renders and the list read succeeds, a key is created in the browser and listed, the gateway confirms the created key, the key is revoked in the browser and marked Revoked, and the gateway confirms the revocation. -- falsifier: the golden record's checks are exactly G1-key-surface-rendered-and-list-read-succeeded, G2-created-in-browser-and-listed, G3-gateway-confirms-the-created-key, G4-revoked-in-browser-and-marked-revoked and G5-gateway-confirms-the-revocation, and every one of them is `ok`.
* **AC3** — The golden leg carries a discrimination control that ran and passed in the same run, so a green golden leg cannot be a leg that asserted nothing. -- falsifier: the golden record satisfies `.control.name == "key-list-answers-the-session-and-refuses-no-credential" and .control.ran == true and .control.ok == true`.
* **AC4** — The C6 error leg emits a `key-lifecycle-error` record declaring criterion C6 on the dev plane with result PASS, and its positive control — the same key authenticating successfully on the same endpoint before revocation — ran and passed in that same run. -- falsifier: the error record satisfies `.chain == "key-lifecycle-error" and .kind == "error" and .criterion == "C6" and .target.plane == "dev" and .result == "PASS" and .control.name == "a-live-key-is-accepted-on-the-same-endpoint" and .control.ran == true and .control.ok == true`.
* **AC5** — Revocation refuses: the revocation is accepted, the revoked key stops authenticating, and the active list excludes it afterwards. -- falsifier: the error record's checks include E0-revocation-accepted, E1-revoked-key-is-rejected and E2-list-after-revoke-excludes-it, and every one of them is `ok`.
* **AC6** — Key-scoped isolation refuses: the stored hash does not authenticate, and a key belonging to tenant A does not authenticate as tenant B. These two legs are the only mechanical proof of key-scoped tenant isolation anywhere in C6, which is why the operator ruling of 2026-09-12 on this ticket kept them in scope rather than folding them out. -- falsifier: the error record's checks include E3-stored-hash-is-rejected and E4-tenant-a-key-is-refused-as-tenant-b, and both are `ok`.
* **AC7** — The emitted records identify a key by its id and never carry the key value. -- falsifier: no `onxk_`-prefixed value appears anywhere in the downloaded record artifact tree.
* **AC8** — The board reads C6 from both legs' records, which is this ticket's stated closing condition — a named run green AND read by the board, not merely green somewhere. -- falsifier: omninode_infra#1319 is MERGED at `6246806bc73d2223128cd8b25446ae1da0103d3a` and `tools/beta_board/beta_board_data.py` at that ref carries the row `("m3-key-lifecycle-chain","OmniNode-ai/omniweb","m3-key-lifecycle-chain.yml","C6")`.

<!-- onex:dod-acceptance-criteria:end -->
