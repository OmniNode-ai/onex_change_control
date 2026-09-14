# OMN-18304 live replay fixtures

The real bytes of the union defect this gate refuses, fetched from GitHub rather
than written to match the fix. The layout mirrors the repository so the gate can
be pointed straight at a case directory.

| Case | Source |
| --- | --- |
| `before/` | `contracts/OMN-18291.yaml` at `3d2db1d1cf`, the first companion on this ticket |
| `union/` | the same contract at `be02006e78`, the second companion's union pass, which renamed one evidence item, plus that pass's receipt still carrying the pre-rename binding |
| `repaired/` | the contract and receipt as merged at `bb470410b6`, after the binding was recomputed by hand |

`union/` is the RED case and `repaired/` is the negative control on the same
ticket, which is what distinguishes a gate that catches this defect from one that
catches everything. Do not regenerate these from current code and do not reformat
them: their value is that they are history.
