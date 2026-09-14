## Summary

Prose that is not a criterion, and names no check.

## Acceptance criteria

* **AC1 — the first thing.** Prose restating it. — falsifier: `uv run pytest tests/test_ac1.py -q` asserts it.
* **AC2b — a criterion whose label the grammar cannot parse.** Prose restating it. — falsifier: `uv run pytest tests/test_ac2b.py -q` asserts it.


## Out of scope

Judging whether the bound check proves the criterion.
