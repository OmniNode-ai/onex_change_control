OMN-17922

## What this changes

OCC evidence companions authored by the writer App now arm auto-merge on their
own `pull_request: opened` run, instead of waiting for a `check_suite:
completed` event to re-trigger the Auto-Merge workflow.

`Check OCC eligibility preflight status` took its reading once per event. The
`opened` run reaches that step before the companion's own eligibility check has
even started, so it deferred, and arming fell through to the check_suite
re-attempt path — whose delivery is neither immediate nor one-per-PR. The step
now re-takes the same reading on a bounded poll.

## Why, measured

The first OMN-17922 pass (onex_change_control#8225, 2026-09-04) made bot
companions arm at all. It did not make them arm promptly. Four companions opened
on this repo in one window on 2026-09-18:

| PR | opened | eligibility SUCCESS | armed | wasted |
|---|---|---|---|---|
| 10179 | 08:15:07Z | 08:16:01Z (+54s) | 08:28:27Z | 12m26s |
| 10181 | 08:15:51Z | 08:16:47Z (+56s) | 08:28:28Z | 11m41s |
| 10180 | 08:15:29Z | — | 08:33:42Z | 18m13s |
| 10183 | 08:31:39Z | 08:32:43Z (+64s) | 08:35:47Z | 3m04s |

Eligibility was green inside ~60 seconds every time. The product PR behind each
companion stayed BLOCKED on its `OCC Companion Merged Gate (OMN-15214)` check
for the whole gap, and four lanes hand-armed a companion on the morning of
2026-09-18 to clear it (FRICTION rows at `docs/tracking/ROLLING_WORK_LEDGER.md`
lines 3861, 4040, 4049, and the 2026-09-17 occurrence at 3027).

Read back live before the change:

```
gh pr list --repo OmniNode-ai/onex_change_control --state open \
  --json number,author,createdAt,autoMergeRequest
gh api repos/OmniNode-ai/onex_change_control/commits/<head>/check-runs?per_page=100
```

## What is NOT relaxed

Nothing about *what* is required moved. SUCCESS is still the only state that
arms. A concluded FAILURE still exits 1 with the same message. An exhausted
budget still defers, leaving the existing check_suite path to arm later. An API
error, an unparseable body, a candidate with no start timestamp, and a head with
no eligibility check each still defer, so every ambiguous reading is a
non-arming one.

The poll is scoped to the single shape that measurably suffers. A new
`companion` output on the resolve step is true only for the writer App with an
`evidence(` title — strictly narrower than the existing `arm` output, and read
by nothing except the poll budget. The `jonahgabriel` identity arms and never
polls, so no human PR pays for the wait. The check_suite and workflow_dispatch
paths resolve to a zero budget and take exactly one reading, which is the
pre-change behaviour byte for byte.

The eligibility step keeps `secrets.GITHUB_TOKEN`. The arming credential is
untouched: both merge-state-mutating steps still carry the org PAT, for the
OMN-17875 / OMN-16373 reason recorded in the file — an App-token or
GITHUB_TOKEN-authored merge would starve this repo's three dev-push workflows.

### Deliberately not done

Arming *before* eligibility concludes, on the grounds that `occ-preflight /
eligibility` is a required status check on `dev` and GitHub would hold the merge
anyway. That is true today (read back live: `required_status_checks.contexts`
lists it), but it is a property of branch protection this workflow cannot read
with `secrets.GITHUB_TOKEN`; making it readable would put the PAT on a third,
read-only step. A gate that stays fail-closed on its own evidence is worth more
than the last ~60 seconds.

## Evidence

`tests/ci/test_auto_merge_companion_poll_omn17922.py` executes the step's own
bash against a scripted Checks API rather than re-implementing it: pending then
SUCCESS arms with no check_suite event; a zero budget takes exactly one reading
and defers; a concluded FAILURE and `action_required` each exit 1; an exhausted
budget defers; and an erroring API, a junk body, a missing start timestamp and
an absent eligibility check each defer. It also asserts the budget expression
names both halves of its scope and falls back to zero, and that the step does
not take the arming PAT.

Local run of every Auto-Merge test module in this repo:

```
uv run pytest tests/ci/test_auto_merge_companion_poll_omn17922.py \
  tests/ci/test_auto_merge_bot_companion_arming_omn17922.py \
  tests/ci/test_auto_merge_hold_omn18179.py \
  tests/ci/test_auto_merge_governance_exclusion.py \
  tests/ci/test_auto_merge_eligibility_and_strategy_omn18074.py -q
109 passed
```

AC1 of the ticket is proven after merge, on the next App-authored companion:
`autoMergeRequest.enabledBy` set inside the Auto-Merge job's own execution
window, with no hand `gh pr merge` in any lane's history. That readback is
appended to the ticket.

This PR is opened as the writer App through the OMN-18327 dispatch path, because
it touches `.github/` and a human-authored PR on a privileged path is refused by
`check-human-authored-privileged-pr`.
