Quoted cause from the owning ticket: "Nothing anywhere reads whether a named enforcement surface exists on the branch the work actually targets."

The existing drift effect node and tripwire now route committed enforcement claims through HandlerEnforcementPlacement. The handler reads the source, workflow producer and live required_status_checks on the claim's governed branch. Missing sources, missing contexts, wrong-branch producers and unreadable protection cannot certify an active claim. The node contract declares the command and result topics, and CI and pre-commit use the same handler.

Focused verification on h201:
- New placement suite: 37 passed; original tripwire suite: 79 passed.
- Changed-file Ruff and mypy checks passed.
- Both previously failed hooks now pass: the imperative contract guard and canonical-file-shape ratchet. The node remains declarative, the handler uses public transport adapters, and the serialized verdict is validated without suppression comments.
- The enforcement-surface-placement pre-commit hook passed, including its live main read.
- The full live tripwire passed all four facts.
- Live positive control: one committed surface resolves against main protection.
- Live negative control: a named source absent from main returns passed=false, with one surface checked.

Lab: host=h201 lane=lab-fill-omn18945-10080430 command=uv run pytest tests/unit/test_enforcement_surface_placement.py -q --no-cov -p no:cacheprovider observed=37 passed

Remaining for OMN-18945: AC-3 still needs validate-prod-promotion-grants in main's live required contexts; AC-4 still needs main's grant-bearing rollup dependencies to require success. The live protection read and main workflow confirm both gaps. This dev change supplies the general placement mechanism; those main changes remain necessary before ticket closeout. The new bus operation is declared and handler-tested; deployed bus registration has not been exercised in this lane.
