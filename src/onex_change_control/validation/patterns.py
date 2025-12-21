"""Validation patterns for schema models.

Shared regex patterns used across multiple model files to ensure consistency.
"""

import re

# SemVer pattern for schema_version validation
# Note: This pattern supports basic SemVer (major.minor.patch) only.
# Pre-release versions (e.g., "1.0.0-alpha") and build metadata (e.g., "1.0.0+build")
# are not supported. If full SemVer support is needed, consider using a SemVer library.
# Pattern rejects leading zeros (e.g., "01.0.0" is invalid) per SemVer spec.
SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
