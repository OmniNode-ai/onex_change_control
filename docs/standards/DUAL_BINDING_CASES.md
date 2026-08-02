<!-- SPDX-FileCopyrightText: 2026 OmniNode.ai Inc. -->
<!-- SPDX-License-Identifier: MIT -->

# Dual-binding cases — the harness convention

**OMN-15669, operator ruling R-0802-9 (2026-08-02).**
Authority for the shape itself: [`schemas/occ_contract_v1.schema.yaml`](../../schemas/occ_contract_v1.schema.yaml).
Engine: `src/onex_change_control/validation/contract_shape_v1.py`.
Harness: `src/onex_change_control/testing/seam_binding.py`.

## The rule

**One test body, one parameterized fixture axis, two bindings.** A case declared
`bindings: both` in a contract runs the *identical* body mock-bound (fast, no infra)
and real-bound (the integration / golden-chain run). Duplicating the body into a
separate integration module is the anti-pattern this axis exists to forbid: two
bodies drift, one body cannot.

```python
from onex_change_control.testing.seam_binding import assert_seam_shape, binding_params

SEAM = "onex_change_control.models.model_seam_binding.ModelCollectorSeam"

@pytest.mark.parametrize("binding", binding_params("both"))
def test_widget_store_seam(binding: str) -> None:      # <- case id == function name
    dep = MockStore() if binding == "mock" else RealStore()
    payload = dep.fetch("w-1")
    assert_seam_shape(payload, SEAM, binding=binding)   # SAME schema, both legs
    assert payload["widget_id"] == "w-1"
```

`binding_params(declared)` takes the contract's own `bindings` value, so the axis
and the declaration cannot drift: the gate reads the collected param ids back and
fails with `binding_axis_mismatch` when they disagree. `binding_params("mock")`
emits only `[mock]`; `binding_params("both")` emits `[mock]` and `[real]`, and the
`real` param carries `pytest.mark.integration`.

## Reuse, not parallel machinery

The real-bound leg is the **existing** OCC golden-chain path, not a new one
(net-negative-surface rule):

| Leg | Selector | Existing convention it reuses |
|---|---|---|
| mock-bound | `pytest -m 'not integration'` (or no marker filter) | `tests/test_golden_chain_*.py` — mock at a *named boundary*, e.g. `patch("...governance_emitter._try_produce")` |
| real-bound | `pytest -m integration` | `tests/integration/**` — the repo's registered `integration` marker, already declared in `pyproject.toml` |

No new marker, no new runner, no new fixture plugin, no second conftest. The only
new surface is `binding_params` + `assert_seam_shape`, and both exist to *remove*
the hand-written mock/real duplication they replace.

## Mock/real divergence is a seam defect, not a flake

`assert_seam_shape` validates the mock payload and the real payload against the
**same** `seam_schema` the contract declares for that dependency. A mock shaped
like something the real dependency could never produce therefore fails on the
*fast* leg — this is the OMN-15598 class, made unrepresentable rather than merely
discouraged.

If the mock-bound leg of a case is green and the real-bound leg of the *same* case
is red, that is a **reportable seam defect**: the two implementations disagree
about a contract they both claim to satisfy. Do not mark it flaky, do not re-run
it, do not narrow the case. File it (second failure of the same check is a bug,
not a flake) and fix the seam.

## What the gate asserts

For every dependency in the contract:

1. `seam_schema` **resolves** — a file in the tree, or an importable pydantic model
   (`seam_schema_unresolvable`);
2. some case **covers** `dependency:<name>` (`seam_case_missing`);
3. that case is declared `bindings: both` (`seam_case_not_dual_bound`);
4. the case's test file **cites** the same `seam_schema` string
   (`seam_schema_not_cited`);
5. the case's test file **executes** `assert_seam_shape(` — citing a schema in a
   docstring is not validation (`seam_validation_not_executed`);
6. the collected parameterized axis matches the declaration
   (`binding_axis_mismatch`).
