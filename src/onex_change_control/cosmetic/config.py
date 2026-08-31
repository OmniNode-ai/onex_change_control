# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Load and parse the canonical cosmetic spec."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_SPEC_PATH = Path(__file__).parent / "spec.yaml"


def load_spec(spec_path: Path | None = None) -> dict[str, Any]:
    """Load the cosmetic spec from a YAML file.

    Args:
        spec_path: Path to the spec file. Defaults to the embedded spec.yaml.

    Returns:
        Parsed spec as a dictionary.

    Raises:
        FileNotFoundError: If the spec file does not exist.
        TypeError: If the spec file is not a YAML mapping.

    """
    path = spec_path or _DEFAULT_SPEC_PATH
    if not path.exists():
        msg = f"Spec file not found: {path}"
        raise FileNotFoundError(msg)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        msg = f"Spec file must be a YAML mapping, got {type(data).__name__}"
        raise TypeError(msg)
    return data
