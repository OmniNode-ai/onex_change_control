"""Test that the package imports cleanly."""

import re

import onex_change_control


def test_package_imports() -> None:
    """Test that the package can be imported."""
    # Verify __version__ exists and has valid semver format
    assert hasattr(onex_change_control, "__version__")
    assert isinstance(onex_change_control.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+$", onex_change_control.__version__) is not None
