"""Who may do what, as pure functions of roles — no database, no request.

PLATFORM_ADMIN is a strict superset of OWNER: it passes every check that
OWNER passes (see `role_satisfies`), and is additionally the only role that
may touch another PLATFORM_ADMIN account. Nothing here widens ACCOUNTANT or
BUYER.
"""

from collections.abc import Iterable

from models.enums import Role

# Roles that use the Trading Console (everything except the buyer PWA).
CONSOLE_ROLES: tuple[Role, ...] = (Role.OWNER, Role.ACCOUNTANT, Role.PLATFORM_ADMIN)

# Roles that must enrol in TOTP MFA when MFA enforcement is on (§14).
MFA_ROLES: tuple[Role, ...] = (Role.OWNER, Role.ACCOUNTANT, Role.PLATFORM_ADMIN)


def role_satisfies(role: Role, allowed: Iterable[Role]) -> bool:
    """True if `role` may pass a route that allows `allowed`. PLATFORM_ADMIN
    passes wherever OWNER does; it never passes a BUYER-only route, because
    those are a buyer's own personal data and not something an owner reaches."""
    allowed = tuple(allowed)
    return role in allowed or (role is Role.PLATFORM_ADMIN and Role.OWNER in allowed)


def can_assign_roles(actor: Role, *roles: Role) -> bool:
    """Creating an account with, or changing an account to/from, PLATFORM_ADMIN
    is reserved to a PLATFORM_ADMIN; an OWNER can never mint or alter one."""
    if Role.PLATFORM_ADMIN in roles:
        return actor is Role.PLATFORM_ADMIN
    return True


def can_manage_account(actor: Role, target: Role) -> bool:
    """Deactivate / reactivate / change role / view or reset another account:
    a PLATFORM_ADMIN account is out of reach for everyone but a PLATFORM_ADMIN."""
    return target is not Role.PLATFORM_ADMIN or actor is Role.PLATFORM_ADMIN


def can_set_temporary_password(actor: Role, target: Role) -> bool:
    """Resetting someone's password lets the resetter sign in as them, so it is
    narrower than the rest of account management: an OWNER may reset an
    ACCOUNTANT or BUYER, never another OWNER; only a PLATFORM_ADMIN may reset
    an OWNER or another PLATFORM_ADMIN."""
    if actor is Role.PLATFORM_ADMIN:
        return True
    if actor is Role.OWNER:
        return target in (Role.ACCOUNTANT, Role.BUYER)
    return False
