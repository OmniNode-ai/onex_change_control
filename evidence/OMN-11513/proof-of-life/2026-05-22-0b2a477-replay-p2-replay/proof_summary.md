# Proof-of-Life Summary — P2.F `--replay` honesty receipt (Day 4)

> **Replay-mode run — no provider API call occurred; fixture-driven only.** This run exists explicitly to provide the honesty receipt that `--replay` does not call any LLM provider. `ONEX_TRACK_A_API_KEY` was explicitly UNSET via `env -u ONEX_TRACK_A_API_KEY` before invocation.

**Run owner:** clover
**Run date (UTC):** 2026-05-22T17:40:48Z
**Run ID:** `5f6c524e-99e3-4835-b1d8-78368006547a`
**Outcome:** **PASS**

## Repo, branch, and commit

- **Repo:** `OmniNode-ai/onex-self-extending-agent`
- **Branch:** `main`
- **Commit SHA:** `0b2a477e5a377e83abfd4f2289f7eeedb86055a2`
- **Working-tree state during run:** `src/agent/agent.py` was modified at this moment (the temporary patch for P1), but the `--replay` path does not load `src/agent/agent.py`, so the patched state is irrelevant to this run. The patch was reverted before the report was finalized.

## Exact command

```bash
env -u ONEX_TRACK_A_API_KEY uv run python -m src --replay docs/evidence/golden/golden_fixture.json
```

Note the `env -u ONEX_TRACK_A_API_KEY` prefix — explicit removal of the API key from the child process's environment to prove no provider call can occur. This matches the recorded canonical command in `docs/demo/demo_manifest.json`.

## Mode

- **Declared:** `replay`
- **Actually:** replay-only (verified by zero matches of HTTP/provider strings in stdout; `[REPLAY]` mode banner present)

## Fixture

- **Path:** `docs/evidence/golden/golden_fixture.json` (in the hackathon repo)
- **Size:** 2497 bytes
- **SHA-256:** `527c0de61217aaf33d2abdadb3dfc05e633c5a7512593043a7ac122c6da6a4d1`

## Step-by-step outcome

| Step | Outcome | Evidence |
|---|---|---|
| Banner | PASS | `[MODE:REPLAY] [STAGE:AGENT] Golden fixture replay` (line 1 of stdout) |
| Step 1: Validate contract + handler | PASS | `schema`, `syntax`, `security` all PASS; `validator_version=1.0.0` |
| Step 2: Register tool via ToolRegistry | PASS | `node_name: node_sentiment_classifier`; `hash: sha256:41b4ffbed...` |
| Step 3: Invoke with fixture input | PASS | Input `{'text': 'This product is amazing!'}` → Output `{'sentiment': 'positive', 'confidence': 0.99}` |
| Honesty closer | PASS | Final line: `[REPLAY] Done — no LLM calls made.` |

Exit code: `0`. Total stdout: 19 lines. Wall-clock: sub-second.

## Honesty receipt — five-point check

1. **Key state at invocation:** `env -u ONEX_TRACK_A_API_KEY` removed `ONEX_TRACK_A_API_KEY` from the child process environment. Confirmed.
2. **Provider-call markers in stdout:** `grep -ciE 'httpx|429|200 OK|Content-Type|generativelanguage|gemini|provider|Bearer|api_key|httpcore'` over `replay-stdout.log` returned **0** matches. No HTTP traffic indicator of any kind.
3. **Mode banner visibility:** `[MODE:REPLAY]` appears on line 1; every subsequent step line is prefixed with `[REPLAY]`. The disclosure is loud and consistent.
4. **Fixture provenance shown in stdout:** line 3 reads `[REPLAY] Fixture: golden_fixture.json`. The replay names the file it's reading from.
5. **Fixture or config modifications required to make replay work:** NONE. The replay worked on the as-shipped `docs/evidence/golden/golden_fixture.json` with zero local edits. This is the "if replay only works after modifying local config/fixtures, document as a blocker" check from the original P2 brief — explicitly NOT a blocker here.

## Whether MCP registration/invocation was live, simulated, or replayed

**Replayed.** Both registration (Step 2) and invocation (Step 3) were performed against fixture data and a fixture-loaded contract. No real MCP runtime was contacted; no provider was called. The `[REPLAY]` prefix on each line and the explicit `Done — no LLM calls made.` closer correctly disclose this.

## Relevance to OMN-11482

This replay run **exercises the invoke path structurally** (Step 3 calls into the same `invoke_generated_tool` family of code that OMN-11482's fix (PR #105 / commit `996317f`) modified). It does NOT validate OMN-11482 as a LIVE proof because the replay invocation does not go through the same `_InputProxy` wrap that an ADK-driven live invocation would. But it does confirm the invoke-step logic is **structurally functional** when given a valid contract + handler + input. Worth flagging for the OMN-11482 close decision.

## Bottom line

- **--replay = PASS**
- **No LLM calls made — verified**
- **Mode disclosure clean — `[REPLAY]` on every line**
- **Fixture sha256 captured for replay reproducibility**
- **OMN-11482 invoke-step logic is structurally functional in the replay path** (separate from the LIVE-proof question)
- **This is the demo-fallback path that works today.** Even with the live `--agent` path blocked on quota / registry hardcode, the demo recording at `docs/demo/replay-demo.cast` is recorded against this exact path and would render correctly.
