<!-- SPDX-FileCopyrightText: 2026 OmniNode.ai Inc. -->
<!-- SPDX-License-Identifier: MIT -->

# contract_shape_v1 fixtures (OMN-15669)

## The two historical blobs

`OMN-15413.at-acc18c627.embed.yaml.txt` and `OMN-15413.at-f6197f0b3.rewrite.yaml.txt`
are **verbatim byte copies** of `contracts/OMN-15413.yaml` at two real commits in
this repository:

| Fixture | Commit | What it is |
|---|---|---|
| `…at-acc18c627.embed.yaml.txt` | `acc18c627f8450693f8a169cab26d2d9e87f7898` (2026-08-02 07:16:10 -0700, *evidence(OMN-15413,OMN-15421): add 20 PASS receipts from live check execution*) | the contract as it stood when its receipts were minted — i.e. what a ticket body would have embedded |
| `…at-f6197f0b3.rewrite.yaml.txt` | `f6197f0b3560533e46db0ce540feb3a3120c235a` (2026-08-02 07:19:20 -0700, *fix(OMN-15413): repair executable OCC evidence*) | the in-place rewrite that landed 3 minutes later |

`test_identity_block_divergence` replays exactly that pair and requires the
identity leg to go RED with a unified diff.

**The `.txt` suffix is load-bearing.** These files must stay byte-identical to
their commits, and a `.yaml` suffix would put them under `yamlfmt` (which
reflows and can inject the OMN-15479 contamination sentinel) and under
`validate-string-versions` (which rejects their historical
`schema_version: "1.0.0"`). Reformatting the evidence would destroy the very
divergence the test measures. Verify at any time:

```bash
diff <(git show acc18c627:contracts/OMN-15413.yaml) \
     tests/fixtures/contract_shape_v1/OMN-15413.at-acc18c627.embed.yaml.txt
diff <(git show f6197f0b3:contracts/OMN-15413.yaml) \
     tests/fixtures/contract_shape_v1/OMN-15413.at-f6197f0b3.rewrite.yaml.txt
```

## The conformant GREEN fixture tree

`conformant/` is a miniature repo the gate evaluates end to end: a v1 contract at
`conformant/contracts/v1/OMN-99999.yaml`, its seam schema, and a real case module
the gate collects through `pytest --collect-only`. `tests/conftest.py` excludes
that module from the outer suite via `collect_ignore_glob` — it is data the gate
collects, not a test of this repo.
