# Wire Schema Contract Specification

> Ticket: OMN-7357 | Status: Active | Version: 1.0.0

## Purpose

Wire schema contracts are the single source of truth for cross-repo Kafka topic
field schemas. Every Kafka topic that crosses a repository boundary must have a
wire schema contract declaring required and optional fields with canonical names.

Producer code, consumer models, and CI gates all derive from these contracts.

## YAML Structure

```yaml
topic: "onex.evt.<producer>.<event-name>.v<n>"
schema_version: "1.0.0"
ticket: "OMN-XXXX"
description: "..."

producer:
  repo: "<repo>"
  file: "<path to producer code>"
  function: "<emitting function>"

consumer:
  repo: "<repo>"
  file: "<path to consumer code>"
  model: "<Pydantic model class>"
  ingest_shim: "<optional shim model>"              # optional
  ingest_shim_retirement_ticket: "OMN-XXXX"         # optional

required_fields:
  - name: "<field_name>"
    type: "<type>"        # uuid, string, float, integer, datetime, boolean, array, object
    description: "..."
    constraints: {}       # optional: ge, le, min_length, max_length, enum

optional_fields:          # optional section
  - name: "<field_name>"
    type: "<type>"
    nullable: true
    description: "..."

renamed_fields:           # optional — tracks active shims
  - producer_name: "<old_name>"
    canonical_name: "<new_name>"
    shim_status: "active|retired"
    retirement_ticket: "OMN-XXXX"

collapsed_fields:         # optional — fields collapsed into other fields
  - name: "<field_name>"
    note: "Collapsed into metadata dict"

ci_gate:                  # optional
  test_file: "<path to handshake test>"
  test_class: "<class name>"
```

## Rules

1. `required_fields` must be present (may be empty).
2. No duplicate field names within `required_fields` or `optional_fields`.
3. No field name may appear in both `required_fields` and `optional_fields`.
4. Field `type` must be one of: uuid, string, float, integer, datetime, boolean, array, object.
5. `producer` and `consumer` sections are required.
6. `topic` and `schema_version` are required.

## Pydantic Model

The contract is validated by `ModelWireSchemaContract` in
`onex_change_control.models.model_wire_schema_contract`.

## Precedent

`omnibase_infra/src/omnibase_infra/services/observability/agent_actions/contracts/routing_decision_v1.yaml`
was the first wire schema contract, hand-authored for OMN-3425. This spec generalizes that pattern.
