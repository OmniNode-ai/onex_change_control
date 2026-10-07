# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Re-export of core ModelTicketContract and OCC supporting types. OMN-10066

ModelTicketContract is re-exported from omnibase_core so all consumers share
a single class identity. The remaining types (ModelDodCheck, ModelDodEvidenceItem,
ModelEmergencyBypass, ModelEvidenceRequirement) are OCC-local models kept in
model_dod_check.py and re-exported here for backwards-compatible import paths.
"""

from omnibase_core.models.ticket.model_ticket_contract import (
    ModelTicketContract as ModelTicketContract,  # re-export
)

from onex_change_control.models.model_dod_check import (
    ModelDodCheck as ModelDodCheck,  # re-export
)
from onex_change_control.models.model_dod_check import (
    ModelDodEvidenceItem as ModelDodEvidenceItem,  # re-export
)
from onex_change_control.models.model_dod_check import (
    ModelEmergencyBypass as ModelEmergencyBypass,  # re-export
)
from onex_change_control.models.model_dod_check import (
    ModelEvidenceRequirement as ModelEvidenceRequirement,  # re-export
)

__all__ = [
    "ModelDodCheck",
    "ModelDodEvidenceItem",
    "ModelEmergencyBypass",
    "ModelEvidenceRequirement",
    "ModelTicketContract",
]
