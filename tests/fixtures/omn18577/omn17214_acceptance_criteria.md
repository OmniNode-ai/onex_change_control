## Acceptance criteria (falsifiable)

**AC1** — `node_gateway_link_health_projection_compute` has rows in `consumer_flow_windows`, and its verdict is `STALLED` (in > 0, out == 0), on the live dev lane. **Falsified by** zero rows, or by any state other than `STALLED` while `gateway-link-health-upsert.v1` HWM stays 0 and `gateway-heartbeat.v1` advances. This is plan §4.2 item 3, still open.

**AC2** — The count of live `Stable` (group, topic) subscriptions with zero flow rows over a 10-minute window drops from 57 to the set that is genuinely off the auto-wiring path, and that residual set is **enumerated in the ticket by name with the reason each is off-path** — not left as a number. **Falsified by** reporting a smaller number without the residual enumeration.

**AC3** — A reducer that publishes to its declared `publish_topics` reports `messages_out > 0`. Concretely: `local.omnimarket.projection_consumer_flow` reads `FLOWING`, not `STALLED`, in a window in which `projection-consumer-flow-applied.v1` advances. **Falsified by** the Phase 1 writer continuing to report itself stalled.

**AC4** — After AC3, the STALLED population over a 10-minute window contains no pair whose declared output topic advanced during that window. **Falsified by** any surviving producing-but-STALLED pair.

**AC5** — A hermetic test drives BOTH wiring branches (`_make_event_bus_callback` and `_make_raw_event_projection_callback`) and asserts a registered counter key exists for each. **Falsified by** a test that only covers the branch that already works — the defect is precisely that one branch was never exercised.

**AC6** — A gate (CI or contract-validation) fails when a new subscription path is added that does not register a flow counter. Per Operating Rule 5, detection that is not enforcement gets ignored; this defect is itself an instance of an un-gated seam drifting.
