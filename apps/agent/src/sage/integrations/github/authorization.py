"""Pure repository-permission decisions for GitHub invocations."""

_AUTHORIZED_PERMISSIONS = frozenset({"admin", "write"})


def is_authorized_permission(permission: str) -> bool:
    """Return whether GitHub's calculated legacy permission may run Sage."""

    return permission in _AUTHORIZED_PERMISSIONS
