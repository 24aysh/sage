import pytest

from sage.integrations.github.gate import is_authorized_permission


@pytest.mark.parametrize("permission", ["admin", "write"])
def test_write_level_permissions_are_authorized(permission: str) -> None:
    assert is_authorized_permission(permission) is True


@pytest.mark.parametrize(
    "permission",
    ["read", "none", "triage", "maintain", "", "ADMIN", "custom-role"],
)
def test_other_permission_values_are_denied(permission: str) -> None:
    assert is_authorized_permission(permission) is False
