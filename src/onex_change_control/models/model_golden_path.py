# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Re-export of core golden path models. OMN-10066"""

from omnibase_core.models.ticket.model_golden_path import (
    ModelGoldenPath as ModelGoldenPath,  # re-export
)
from omnibase_core.models.ticket.model_golden_path_assertion import (
    ModelGoldenPathAssertion as ModelGoldenPathAssertion,  # re-export
)
from omnibase_core.models.ticket.model_golden_path_input import (
    ModelGoldenPathInput as ModelGoldenPathInput,  # re-export
)
from omnibase_core.models.ticket.model_golden_path_output import (
    ModelGoldenPathOutput as ModelGoldenPathOutput,  # re-export
)

__all__ = [
    "ModelGoldenPath",
    "ModelGoldenPathAssertion",
    "ModelGoldenPathInput",
    "ModelGoldenPathOutput",
]
