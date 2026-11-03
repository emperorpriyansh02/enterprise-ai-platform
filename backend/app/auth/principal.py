"""Who is making the request: the signed-in user (from a verified token), or the dev user."""

from dataclasses import dataclass, field

from app.auth.permissions import ADMIN, permissions_for, roles_allow


@dataclass(frozen=True)
class Principal:
    subject: str  # the identity provider's stable user id (`sub`)
    username: str  # `preferred_username`, e.g. "alice"; stored as `created_by`
    name: str | None = None
    email: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    is_dev: bool = False  # the built-in user when AUTH_ENABLED=false

    @property
    def permissions(self) -> frozenset[str]:
        return permissions_for(self.roles)

    @property
    def is_admin(self) -> bool:
        return ADMIN in self.roles

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def may_use(self, allowed_roles: list[str] | None) -> bool:
        """Passes a resource's `allowed_roles` rule (empty = everyone; admins always pass)."""
        return roles_allow(self.roles, allowed_roles)


def dev_principal(roles: list[str]) -> Principal:
    """The user every request acts as when AUTH_ENABLED=false (local development only)."""
    return Principal(subject="dev", username="dev", name="Developer", roles=frozenset(roles), is_dev=True)
