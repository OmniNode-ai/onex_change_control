# P4 Clean-Clone Judge Verification — SEA `dev` @ `fc5eed3`

**Date:** 2026-06-11
**Owner:** Bret (clone45)
**Role:** verifier, not author
**Umbrella:** OMN-11513 · **Verifies:** OMN-12894 (JUDGE_REPRODUCE.md), corroborates OMN-12902 (overlay-required live CLI)
**Execution mode:** local (fresh clone, WSL2/Linux)
**Authority mode:** `replay-derived` (golden-fixture replay; no live model read)

## Verdict

**PASS.** A judge can reproduce the SEA Tier 1 no-key path from a fresh clone with standard tooling, no source edits, no overlays, no model key, and no `<onex-host>`/broker dependency. Tag-ready at `fc5eed3`.

## Acceptance bar (per the 2026-06-11 P4 assignment)

| Criterion | Result |
|---|---|
| Fresh clone from `github.com/OmniNode-ai/onex-self-extending-agent` | PASS (`rm -rf` then `git clone` into `/tmp/sea-judge-clean`) |
| Branch is `dev` | PASS ("Already on 'dev'", up to date with origin) |
| `JUDGE_REPRODUCE.md` present | PASS (`ls -l` → 7755 bytes) |
| `uv sync` completes, no manual repair | PASS (cold run: full resolve + download + build of 240 packages incl. 7 git-source deps; warm run: instant) |
| Targeted tests pass | PASS — 31 passed (`tests/unit/test_replay.py`, `tests/unit/test_agent_orchestrator.py`) |
| Golden-fixture replay passes | PASS — validate (schema/syntax/security) → register `node_sentiment_classifier` → invoke → `{'sentiment':'positive','confidence':0.99}`, no LLM calls |
| No source edits / overlays / keys / `<onex-host>` | PASS — confirmed; `ONEX_TRACK_A_API_KEY=(unset)` |

## Run identity

| Field | Value |
|---|---|
| Repo | `onex-self-extending-agent` |
| Branch | `dev` |
| Commit | `fc5eed3a7bcf87addbe0e2f8d10a8122cc03a7f2` |
| Toolchain | uv 0.11.6 (x86_64-unknown-linux-gnu), CPython 3.12.13 |
| Golden task | "Classify customer review sentiment as positive, neutral, or negative with confidence" |
| Captured (UTC) | 2026-06-11T15:09:05Z (verbatim) · 2026-06-11T15:31:52Z (cold-cache) |

## Two runs

1. **Verbatim** (`transcript-verbatim.txt`) — the P4 assignment's exact command list, run by hand on the contractor WSL2 seat. PASS. (`uv` install served from a warm package cache, so instant.)
2. **Cold-cache** (`transcript-coldcache.txt`) — `uv cache clean` first (removed 349,191 files / 26.5 GiB), then the same sequence, so the cold first-install path a first-time judge hits is exercised. The transcript shows the real download/build of all dependencies including the seven git-source packages (omnibase_infra, omnimemory, omnibase_compat, omnimarket, omnibase_spi, omnibase_core, onex_change_control); it completed cleanly in ~7.7s with no manual repair. PASS.

Both runs are on the same SHA with identical functional results.

## Environment statement

No source edits, no local overlays (`contract.local.yaml` / `model_registry.local.yaml` absent), no model key (`ONEX_TRACK_A_API_KEY` unset), no Tailscale / `<onex-host>` / broker dependency. This is the portable, key-free Tier 1 path only.

## Scope / caveats

- Tier 1 **no-key** path only. The live `--agent` path is out of scope here; it is sanctioned-live and correctly fails closed without an approved overlay (verified separately, observation on OMN-12902).
- `replay-derived`: the golden replay reconstructs from the committed fixture and makes no live model call.

## Artifacts in this bundle

- `transcript-verbatim.txt` — by-hand verbatim run.
- `transcript-coldcache.txt` — cache-cleared cold-install run.
