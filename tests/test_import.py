"""Test that the package imports cleanly."""

import onex_change_control


def test_package_imports() -> None:
    """Test that the package can be imported."""
    assert onex_change_control.__version__ == "0.1.0"
