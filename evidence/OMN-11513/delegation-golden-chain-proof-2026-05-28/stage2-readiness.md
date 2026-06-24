# Stage-2 Live-Proof Readiness (OMN-12294) — stability-test lane

Date: 2026-05-28
Author: reconcile-topics
Lane: stability-test ONLY (the runtime host — runtime :18085, effects :18086, Redpanda :39092, Postgres :15436)

## What is ready

- Code change (bridge deleted, 2 native handlers added) committed + pushed:
  - omnimarket branch `jonah/omn-12294-stage2-delete-bridge` @ `2d9ce644f211ba06d58c75ea572e95c0538d982c`
  - omnibase_infra branch `jonah/omn-12294-stage2-delete-bridge` @ `2d67d8dc1d072bb854f1c2f2b82bd62f2fad1749`
  - Both verified on origin.
- Stability-test lane is healthy on the runtime host: runtime + effects v0.37.2 (Up, healthy), Redpanda :39092 reachable, all 6 ombase-infra chain topics already exist.
- LLM endpoint :8001 (Qwen3.6-27B) healthy on the runtime host → the call effect can do a live inference, so the chain can reach `delegation-completed` (pass), not just `delegation-failed`.
- Publish mechanism known (from prior run): publish to `onex.cmd.omnibase-infra.delegation-request.v1` with the **v2.1.0 envelope** (`source_tool` + `envelope_version: 2.1.0`); the lightweight envelope is silently dropped.

## Prior run (DEGRADED) — what my change fixes

`proof_results.json` (correlation ec7466b9, this dir): command submitted PASS, then
`routing-decision MISSING`, all downstream MISSING → DEGRADED. With the bridge in
the deployed image, the chain dies at hop 1. The companion `chain-evidence.md`
also documents the old (now-false) belief that the quality-gate reducer has no
event_bus and "the bridge acts as the in-process glue" (line 180) — exactly what
this stage overturns: the reducers are now native bus consumers.

## The execution-path fork (needs lead decision)

To deploy my branches to the stability-test image, the standard tooling does not
cleanly apply:
- `~/.omnibase/infra/deployed/0.37.2/scripts/deploy-runtime.sh` reads omnimarket/
  compat/occ refs from an `omni_home` clone path that is NOT present on .201
  (no `~/omni_home/omnimarket` etc.). Active deploy-src is a release snapshot
  (`~/.omnibase/deploy-src/omnibase-release-2026-05-27/omnibase_infra`).
- The Kafka-driven `deploy-agent.service` (rebuild orchestrator, fired via
  `deploy-trigger.py --git-ref ...`) is **inactive/dead** (consistent with the
  launchd/systemd-doesn't-fire constraint on this setup).

Two viable manual paths, both manual shared-infra ops on the runtime host:
  (1) Start deploy-agent + publish a signed rebuild command pinned to my branch
      refs, scoped to compose project `omnibase-infra-stability-test`.
  (2) Manual scoped `docker compose -p omnibase-infra-stability-test -f <infra>
      -f <stability> --profile runtime build --build-arg OMNIMARKET_REF=2d9ce644...
      --build-arg VCS_REF=<infra-sha>` from an omnibase_infra source checked out
      at my branch, then `up -d --no-deps --force-recreate omninode-runtime
      runtime-effects` (stability containers only).

Build is heavy (full runtime image + CPU torch, ~10-15 min). Path (2) is the most
direct and lane-scoped; it requires staging my omnibase_infra branch as the build
context on the runtime host (rsync from worktree or git fetch into the deploy-src snapshot).

## Verdict so far

BLOCKED on execution-path decision (1 vs 2) before touching the runtime host build/deploy.
No PR advanced. No bridge reintroduced. Local suite green; chain proven over the
bus in unit/e2e (in-memory) — the remaining gap is the LIVE stability-runtime
proof, which depends on the rebuild path above.
