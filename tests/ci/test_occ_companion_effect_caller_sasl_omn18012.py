# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression pin: both OCC publisher callers must forward repo secrets.

OMN-18012 Phase B enabled SASL/SCRAM-SHA-256 on the dev-lane Redpanda external
listener at ~16:40Z on 2026-09-07. omnimarket's ``config/ci_bus_lanes.yaml``
now DECLARES that transport for the ``dev`` lane, and the canonical publisher
fail-fasts when ``KAFKA_SASL_USERNAME`` / ``KAFKA_SASL_PASSWORD`` are absent
from the job environment rather than publishing unauthenticated or downgrading
the declared protocol.

The omniclaude reusables read those two values from ``secrets.*``, which only
resolve when the CALLER forwards them. ``occ-autobind`` in guards.yml always
did; ``occ-companion-effect`` did not, so the publish step failed in ~9s on
every eligible PR with both variables empty (first seen here on #8667). The
context is not required in this repo, so it failed red while blocking nothing
— the shape that trains lanes to ignore a red check, and it meant the
companion-effect emitter never ran from this repo at all.

Deliberately falsifiable: deleting ``secrets: inherit`` from either job turns
this red. It asserts the forwarding declaration only — no credential value is
read, and secret NAMES are all that appear here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDS_PATH = REPO_ROOT / ".github" / "workflows" / "guards.yml"

# job id -> the reusable filename it is required to call with `secrets: inherit`
OCC_PUBLISHER_JOBS = {
    "occ-companion-effect": "call-occ-companion-effect-reusable.yml",
    "occ-autobind": "call-occ-autobind-reusable.yml",
}


def _guards_workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(GUARDS_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("job_id", "reusable"), sorted(OCC_PUBLISHER_JOBS.items()))
def test_occ_publisher_job_forwards_secrets(job_id: str, reusable: str) -> None:
    """A declared-SASL lane cannot be published to without forwarded credentials."""

    jobs = _guards_workflow()["jobs"]
    assert job_id in jobs, f"guards.yml no longer defines job '{job_id}'"

    job = jobs[job_id]
    assert reusable in str(job.get("uses", "")), (
        f"job '{job_id}' no longer calls {reusable}; this pin needs updating "
        "rather than deleting."
    )
    assert job.get("secrets") == "inherit", (
        f"job '{job_id}' calls {reusable} without `secrets: inherit`. The dev "
        "lane declares security_protocol=SASL_PLAINTEXT / "
        "sasl_mechanism=SCRAM-SHA-256, so the reusable's publish step needs "
        "KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD forwarded from this "
        "caller. Without them the publisher fail-fasts and no OCC command is "
        "emitted from this repo."
    )


def test_occ_publisher_jobs_declare_no_broker_secret() -> None:
    """Only credentials come from secrets; the broker stays in the overlay.

    Negative control for the OMN-14800 silent-repoint surface: an opaque
    bootstrap-servers secret is what let a dev->stability repoint run green.
    OMN-18012 authorised forwarding CREDENTIALS, not reintroducing the broker.
    """

    offending = [
        line
        for line in GUARDS_PATH.read_text(encoding="utf-8").splitlines()
        if "KAFKA_BOOTSTRAP_SERVERS" in line and not line.lstrip().startswith("#")
    ]
    assert not offending, (
        "guards.yml must not inject a bootstrap-servers secret; the dev-lane "
        "broker is declared in omnimarket config/ci_bus_lanes.yaml (OMN-14813). "
        f"Offending lines: {offending}"
    )
