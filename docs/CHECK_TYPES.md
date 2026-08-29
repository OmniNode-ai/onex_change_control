# `check_type` reference — what each DoD check actually does

**Audience:** anyone authoring `dod_evidence` in `contracts/OMN-XXXX.yaml`.

This page exists so you never have to read a runner's source to know what your
check will do. Two different gates execute the checks you write:

| Runner | Where it runs | What it is |
|---|---|---|
| **Hosted** — `Contract Compliance Check` | every PR, in CI | `onex_change_control.scripts.contract_compliance_check` |
| **Local** — `node_dod_verify` | the local Done gate / autoclose evidence sweep | omnimarket `EvidenceCollector` |

Both read the same contract bytes. **For check types both runners execute, this
page states one shared behaviour** — that is the OMN-16824 rule, and it is
enforced by an executed case table
(`tests/fixtures/check_type_runner_semantics.yaml`) that is checked into both
repos byte-for-byte and run against both runners. Rows marked `—` are hosted-only
surfaces until the local runner implements them.

---

## The table

| `check_type` | `check_value` is | Hosted runner | Local runner |
|---|---|---|---|
| `command` | a shell command | **executes it**; exit 0 = PASS | **executes it**; exit 0 = VERIFIED |
| `test_passes` | a shell command that runs tests | **executes it**; exit 0 = PASS | **executes it**; exit 0 = VERIFIED |
| `test_exists` | a glob | globs the workspace | — |
| `file_exists` | a glob | globs the workspace | checks the path exists |
| `grep` | `{pattern, path}` | greps the workspace | — |
| `endpoint` | a URL or path | curls / stats it | — |
| `behavior_proven` | prose attestation | non-executable WARN | non-executable |
| `semantic_grading` | a receipt path | receipt-gate surface | receipt-gate surface |

### `test_passes` is an executed alias of `command`

Write `test_passes` when the command you are running **is a test run**. It runs
exactly like `command`; the distinct name records author intent and is what the
proof-class classifier (OMN-15911) and the substance-floor tier deriver key on.

It does **not** mean "this PR's CI is green". Until OMN-16824 the hosted runner
read it that way — it ignored `check_value` entirely and reported the PR's own
check-run states — so an entry claiming *"the new regression test passes"*
returned PASS whenever unrelated CI was green, including when the named test did
not exist. That reading is gone. If you genuinely want to assert PR CI state,
say so in the open:

```yaml
- check_type: command
  check_value: "gh pr checks 1719 --repo OmniNode-ai/omnimarket"
```

That is a **merge-state** proof, not a behaviour proof, and the classifier
scores it as such.

---

## Where your command runs

* **No `cwd` declared** → the command runs in the **product checkout under
  test** (the repo whose PR is being gated), which the hosted runner passes as
  `--workspace`. Relative paths resolve against it.
* **`cwd` declared** → the command runs **there**. Supported template tokens:
  `${OMNI_HOME}`, `${PR_NUMBER}`, `${REPO}`, `${TICKET_ID}`. A relative `cwd`
  resolves against the workspace; `..` segments are refused.
* **`cwd` declared but unresolvable** → the check is **`NOT_EVALUATED`**, and
  the gate says so out loud. It is **never** rerouted to the workspace: running
  your command in a different tree answers a different question under your
  entry's name.

The hosted gate checks out **one** repo. A `cwd` naming a sibling checkout
(`${OMNI_HOME}/omnimarket`) does not exist there and will be declined. That is
not a bug you should work around with a fabricated path — declare the item's
audience honestly:

```yaml
- id: dod-cross-repo-behaviour
  description: "…"
  execution_scope: local_done_gate   # OMN-15392 — hosted compliance will not evaluate this
  checks:
    - check_type: test_passes
      check_value: "uv run pytest tests/unit/test_thing.py -q"
      cwd: "${OMNI_HOME}/omnimarket"
```

`execution_scope: local_done_gate` items are reported `NOT-EVALUATED` by the
hosted gate by design, and do **not** count toward the hosted
`behavior_proving_count`.

---

## The shell

Commands execute under **`bash -o pipefail -c`** in both runners. A failing
first stage of a pipeline fails the check:

```yaml
# FAILS when the gh call fails, which is what you want.
check_value: "gh api repos/OWNER/REPO/contents/x.py?ref=<sha> --jq .content | base64 -d | grep -q SYMBOL"
```

Under a plain `sh -c` this would have passed on `grep`'s exit code alone.

**The reverse hazard: a passing check going RED via SIGPIPE.** `grep -q`
(and `-qx`/`-qF`) exits at the *first match* and closes its stdin. If the
stage feeding it still has bytes to write when that happens, the write
fails with SIGPIPE and the stage exits 141 — and under `bash -o pipefail -c`
that 141 becomes the whole pipeline's exit status, a **false RED on evidence
that is actually present**. This is exposed by any unbounded producer piped
straight into `grep -q` (a decoded file body, `gh pr diff`, a `git log`
walk, a paginated REST list) — see OMN-15411 Rule E in
`scripts/lint_contract_check_values.py` for the full detector and the
measured producer list.

The fail-closed replacement is a buffered read, asserted with a
**here-string**, not a pipe:

```bash
# SIGPIPE-fragile: printf's write() can be killed once $body is large.
body="$(gh api ... --jq .content | base64 -d)" && printf '%s' "$body" | grep -qF 'MARKER'

# safe: no pipe stage, so nothing can be killed by SIGPIPE.
body="$(gh api ... --jq .content | base64 -d)" && grep -qF 'MARKER' <<< "$body"
```

OMN-16916: the `printf '%s' "$body" | grep -qF` form above was, until this
ticket, the guidance both OMN-15391 Rule D and OMN-15411 Rule E handed out
as *the* sanctioned buffered-read fix — it is still a pipe into an
early-exit `grep -q` consumer, and OMN-15772 measured it reproducing this
exact 141 on its own merged item (10/10 plain-`bash -c` runs exit 0, 10/10
`bash -o pipefail -c` runs exit 141). Use the here-string form for any new
check_value; a corpus-wide sweep of existing instances is tracked under
OMN-16916 AC3.

---

## Two properties every check needs

1. **It must execute.** Prose, an `echo`, or a bare `true` proves nothing.
2. **It must be able to go RED.** A check that reads only files this same
   change authors (the receipt it is stamped in, the contract itself) confirms
   what the author typed, not what the code does. Such checks are reported
   `INERT` and demoted — see `onex_change_control.validation.evidence_admissibility`
   and `admissible_evidence_guidance()`, which the runner prints on refusal.

Timeout: 60s per check in the hosted runner (30s in `node_dod_verify`). A check
that needs longer belongs behind `execution_scope: local_done_gate`, not behind
a bigger number.

---

## References

* OMN-16824 — one semantic for `test_passes`; `cwd` honoured or declined
* OMN-15392 — `execution_scope: local_done_gate`
* OMN-15309 / OMN-14436 — the admissibility predicate and the grandfather ratchet
* OMN-15911 — the proof-class classifier that reads these checks
* `docs/TEMPLATE_GUIDE.md` — the full contract field reference
