# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire type for worker contract (OMN-8408).

Defines the machine-readable contract every spawned Agent (worker) gets at
spawn time. Parallel to ``ModelOvernightContract`` (session-level) and
``ModelSessionContract`` (pipeline-level); this is the per-worker tier.

Used by:

- ``HandlerOvernight`` tick loop — enforces ``heartbeat_interval_seconds`` and
  ``stall_action`` per worker (OMN-8409).
- Task store CAS semantics — ``lease_seconds`` governs claim leases
  (OMN-8414).
- PreToolUse evidence hook — ``required_evidence`` rejects TaskUpdate calls
  that would mark a task completed without the declared evidence (OMN-8410).
- Snapshot writer — ``snapshot_on_tick`` opts a worker into per-tick state
  snapshots (OMN-8412).
- Runbook auto-invocation — ``applicable_runbooks`` lists slugs the overseer
  will match against observed events (OMN-8413).

OMN-10251: ModelEvidenceRequirement renamed to ModelWorkerEvidenceRequirement
and migrated to omnibase_core. ModelEvidenceRequirement is kept as an alias.
"""

from omnibase_core.models.overseer.model_worker_contract import (
    ModelWorkerContract as ModelWorkerContract,
)
from omnibase_core.models.overseer.model_worker_contract import (
    load_worker_contract as load_worker_contract,
)
from omnibase_core.models.overseer.model_worker_evidence_requirement import (
    ModelWorkerEvidenceRequirement as ModelWorkerEvidenceRequirement,
)

ModelEvidenceRequirement = ModelWorkerEvidenceRequirement

__all__ = [
    "ModelEvidenceRequirement",
    "ModelWorkerContract",
    "ModelWorkerEvidenceRequirement",
    "load_worker_contract",
]
