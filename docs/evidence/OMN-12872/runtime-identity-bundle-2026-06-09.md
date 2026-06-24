# OMN-12872 Runtime Identity Bundle - 2026-06-09

Captured by Codex foreground orchestrator on 2026-06-09T20:03:41Z using read-only SSH, Docker inspect,
runtime health, projection health, Redpanda `rpk`, and GitHub remote-ref probes.

## Closure gate result

BLOCKED for final delegation/SEA/escalation closure proof reuse.

The current `stability-test` containers are healthy and wired, but their runtime provenance is not the final
accepted dev source baseline. The running runtime/projection labels and `/app/build-provenance.json` report
`org.opencontainers.image.revision` / `vcs_ref` `89dc580b9f206004b677b99f78e25e0e51aa0d91`, while the current
`omnibase_infra` `dev` ref observed during this pass is `20a42cbf5c01e426cc97929479e29a8b8f7650fe`.

This bundle is valid evidence of current runtime identity and readiness shape, but not sufficient to call
delegation, SEA, escalation, or dashboard closure complete on the final accepted baseline.

## Current source refs

Observed with `git ls-remote ... refs/heads/dev`.

- `omnibase_infra`: `20a42cbf5c01e426cc97929479e29a8b8f7650fe`
- `omnibase_core`: `509df0698d6d8d259b6f1cd4ede9ded9cc3c1977`
- `omnibase_spi`: `a32ce0046710c614248822db0226f3bb5a171d0e`
- `omnibase_compat`: `44cb012aea92067f455e574ba6865cc706f04b35`
- `onex_change_control`: `87e5845125b70846798ae57d156fc0dcd1e2c14f`
- `omnimarket`: `ece1d0458f0aa63b8e23b83cb48914e449d67a9a`
- `onex-self-extending-agent`: `88846acfea8b4081eb13076db859a25691127fad`
- `omnidash`: `dead11e4e975bb8c9c646a1757234f1831f94ca9`

## Running containers

Observed with read-only `docker inspect` on `<user>@<onex-host>`.

| Container | Image | Image digest/id | Health | Started | Runtime/source label |
| --- | --- | --- | --- | --- | --- |
| `omninode-stability-test-runtime` | `omnibase-infra-stability-test-omninode-runtime` | `sha256:97333150335bbf123d886fcf8e0a0620ddd1dc439891d1ff083880af4530f2a0` | healthy | `2026-06-09T12:13:04Z` | `89dc580b9f206004b677b99f78e25e0e51aa0d91` |
| `omninode-stability-test-runtime-effects` | `omnibase-infra-stability-test-runtime-effects` | `sha256:0f6341f5c05ca620d974aa1a5e0259e82ed8c42a33bbfd189af0ba4e8c2d62bc` | healthy | `2026-06-09T12:13:51Z` | `89dc580b9f206004b677b99f78e25e0e51aa0d91` |
| `omnimarket-stability-test-projection-api` | `omnibase-infra-stability-test-projection-api` | `sha256:0510973bc549a93d9dd5b30aa7e40ed3e1e38a08155b2caa807f0efb14c05e8c` | healthy | `2026-06-09T12:13:04Z` | `89dc580b9f206004b677b99f78e25e0e51aa0d91` |
| `omnibase-infra-stability-test-redpanda` | `redpandadata/redpanda:v24.2.7` | `sha256:82a69763bef8d8b55ea5a520fa1b38f993908ef68946819ca1aed43541824c48` | healthy | `2026-06-08T17:41:01Z` | infrastructure |

## Runtime health

Observed with `curl -fsS` from the runtime host loopback.

- Runtime main `:18085/health`: healthy, version `0.38.3`, `event_bus.environment=stability-test`,
  `bootstrap_servers=redpanda:9092`, `subscriber_count=261`, `topic_count=233`, `consumer_count=261`,
  `local_ingress.enabled=true`, `route_count=1745`, active packages
  `omnibase_infra,omnimarket,omniclaude,omniintelligence`.
- Runtime effects `:18086/health`: healthy, version `0.38.3`, `event_bus.environment=stability-test`,
  `bootstrap_servers=redpanda:9092`, `subscriber_count=145`, `topic_count=135`, `consumer_count=145`,
  `local_ingress.enabled=false`.
- Projection API `:13002/health`: `{"status":"ok","postgres":"ok"}`.

## Non-secret runtime markers

Observed with strict allowlisted `env` probes. Values containing credentials were intentionally excluded.

- Main runtime: `ONEX_ENVIRONMENT=stability-test`, `KAFKA_ENVIRONMENT=stability-test`,
  `KAFKA_BOOTSTRAP_SERVERS=redpanda:9092`, `ONEX_RUNTIME_ID=stability-test-main`,
  `ONEX_RUNTIME_ADDRESS=runtime://omninode-pc/stability-test/main`,
  `ONEX_GROUP_ID=onex-stability-test-runtime-main`, `RUNTIME_PROFILE=main`,
  `ONEX_ACTIVE_RUNTIME_PACKAGES=omnibase_infra,omnimarket,omniclaude,omniintelligence`,
  `ONEX_CONTRACTS_DIR=/app/contracts`, `ONEX_RUNTIME_CONTRACTS_DIR=/app/contracts/runtime`,
  `ONEX_STATE_DIR=/app/data/.onex_state_stability_test`, `RUNTIME_SOURCE_HASH=unknown`.
- Effects runtime: `ONEX_ENVIRONMENT=stability-test`, `KAFKA_ENVIRONMENT=stability-test`,
  `KAFKA_BOOTSTRAP_SERVERS=redpanda:9092`, `ONEX_RUNTIME_ID=stability-test-effects`,
  `ONEX_RUNTIME_ADDRESS=runtime://omninode-pc/stability-test/effects`,
  `ONEX_GROUP_ID=onex-stability-test-runtime-effects`, `RUNTIME_PROFILE=effects`,
  `ONEX_ACTIVE_RUNTIME_PACKAGES=omnibase_infra,omnimarket,omniclaude,omniintelligence`,
  `ONEX_CONTRACTS_DIR=/app/contracts`, `ONEX_RUNTIME_CONTRACTS_DIR=/app/contracts/runtime`,
  `ONEX_STATE_DIR=/app/data/.onex_state_stability_test`, `RUNTIME_SOURCE_HASH=unknown`.
- Projection API: `ONEX_ENVIRONMENT=stability-test`, `KAFKA_ENVIRONMENT=stability-test`,
  `KAFKA_BOOTSTRAP_SERVERS=redpanda:9092`, `ONEX_GROUP_ID=onex-runtime`, `RUNTIME_PROFILE=projection-api`,
  `ONEX_ACTIVE_RUNTIME_PACKAGES=omnibase_infra,omnimarket,omniclaude,omniintelligence`,
  `ONEX_CONTRACTS_DIR=/app/contracts`, `ONEX_RUNTIME_CONTRACTS_DIR=/app/contracts/runtime`,
  `RUNTIME_SOURCE_HASH=unknown`.

## Provenance manifest

Observed in `omninode-stability-test-runtime:/app/build-provenance.json`.

- Manifest SHA-256: `d73a79ccd80e0a0f55c0755eb46de509328aba3f4e010439421ed3f85949abb0`
- `build_source`: `workspace`
- `build_time`: `unknown`
- `vcs_ref`: `89dc580b9f206004b677b99f78e25e0e51aa0d91`
- Verified workspace package digests:
  - `omnibase_compat`: `a6bb7af5811e2f33aa2a2b8bc51dc19df441cc6a939af041e8de07918a19bbc8`
  - `onex_change_control`: `be6fc436bead8c36c88c3ef86ee035d21585f61e1829c4ff61f30061041471b1`
  - `omnimarket`: `a111ae056d49c30e768ad13a31126cf46395b64bec40ac477db2e54dd4780ec6`

## Broker shape

Observed with `rpk cluster info --brokers localhost:19092` inside the Redpanda container.

- Cluster: `redpanda.ae26157e-3b35-4b03-a75f-7aa86ce4517f`
- Broker: id `0`, host `100.109.203.94`, port `39092`

In-scope topics exist with six partitions unless noted:

- `onex.cmd.omnimarket.delegate-skill.v1`
- `onex.cmd.omnibase-infra.delegation-request.v1`
- `onex.cmd.omnimarket.node-generation-requested.v1`
- `onex.evt.platform.node-registration.v1`
- `onex.evt.omnimarket.delegate-skill-completed.v1`
- `onex.evt.omnimarket.delegate-skill-failed.v1` (one partition)
- `onex.evt.omnibase-infra.delegation-completed.v1`
- `onex.evt.omnibase-infra.delegation-failed.v1`
- `onex.evt.omnimarket.node-generation-completed.v1`
- `onex.evt.omnimarket.node-generation-failed.v1`
- `onex.evt.omnimarket.projection-delegation-applied.v1`
- `onex.evt.omnimarket.projection-registration-applied.v1`
- DLQ topics including `onex.dlq.commands.v1`, `onex.dlq.events.v1`,
  `onex.dlq.omnibase-infra.commands.v1`, and legacy delegation-specific DLQ topics.

## Consumer groups

Observed with `rpk group describe`.

- `stability-test.omnimarket.node_delegate_skill_orchestrator.consume.1.0.0.__i.stability-test-main.__t.onex.cmd.omnimarket.delegate-skill.v1`:
  `Stable`, one member, total lag `0` across six partitions.
- `stability-test.omnimarket.node_generation_consumer.consume.1.0.0.__i.stability-test-effects.__t.onex.cmd.omnimarket.node-generation-requested.v1`:
  `Stable`, one member, total lag `0` across six partitions.
- `stability-test.runtime_config.delegation-orchestrator.consume.1.0.0.__i.stability-test-main.__t.onex.cmd.omnibase-infra.delegation-request.v1`:
  `Stable`, one member, total lag `0` across six partitions.

Nuance: older or alternate effects-instance groups for lower-level delegation exist with `Empty` state. The active
`stability-test-main` lower-level delegation group is stable with zero lag, so the empty effects groups are not
evidence of missing active dispatch coverage by themselves.

## Required next proof

After the final dev/runtime baseline is deployed to `stability-test` through the approved process, re-run this
bundle and attach its correlation IDs to OMN-12873 delegation proof, OMN-12874 SEA proof, OMN-12875 dashboard
proof, and OMN-12876 escalation proof. Do not use this bundle to mark those lanes closed.
