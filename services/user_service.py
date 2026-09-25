import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    BreachedPassword,
    Conflict,
    Forbidden,
    InvalidToken,
    LastOwnerGuard,
    LastPlatformAdminGuard,
    MfaInvalid,
    NotFound,
    PasswordPolicyViolation,
)
from core.permissions import MFA_ROLES, can_assign_roles, can_manage_account, can_set_temporary_password
from core.security import (
    decode_invite_token,
    generate_totp_secret,
    hash_password,
    is_breached_password,
    password_policy_violations,
    totp_provisioning_uri,
    verify_totp,
)
from models.enums import InviteStatus, Role
from models.user import User
from repositories import refresh_tokens as refresh_token_repo
from repositories import users as user_repo
from services import audit_service


async def _enforce_password_policy(password: str) -> None:
    violations = password_policy_violations(password)
    if violations:
        raise PasswordPolicyViolation(violations)
    if await is_breached_password(password):
        raise BreachedPassword()


async def accept_invite(db: AsyncSession, *, invite_token: str, password: str) -> tuple[User, Role, str | None]:
    """Returns (user, role, totp_provisioning_uri). The URI is only present
    for OWNER/ACCOUNTANT — MFA enrollment is mandatory for them (§14) and
    the client should render it as a QR code immediately. The caller (the
    accept-invite route) logs the user in right after this returns, since
    otherwise an OWNER/ACCOUNTANT could never reach /auth/mfa/confirm —
    the normal login path refuses them until MFA is enrolled."""
    try:
        user_id = decode_invite_token(invite_token)
    except Exception as exc:
        raise InvalidToken("Invite link is invalid or has expired.") from exc

    user = await user_repo.get_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise NotFound("User")
    if user.invite_status != InviteStatus.PENDING:
        raise Conflict("INVITE_ALREADY_USED", "This invite has already been used.")

    await _enforce_password_policy(password)

    role_row = await user_repo.get_role(db, user.id)
    role = role_row.role  # type: ignore[union-attr]

    user.password_hash = hash_password(password)
    user.invite_status = InviteStatus.ACTIVE

    provisioning_uri = None
    if role in MFA_ROLES:
        secret = generate_totp_secret()
        user.mfa_secret = secret
        user.mfa_enrolled = False  # set True once they verify a code, not just receive the secret
        provisioning_uri = totp_provisioning_uri(secret, user.email)

    await audit_service.write(
        db, actor_id=user.id, action="user.accepted_invite", entity="user", entity_id=user.id
    )
    return user, role, provisioning_uri


async def confirm_mfa_enrollment(db: AsyncSession, *, user: User, code: str) -> None:
    if not user.mfa_secret or not verify_totp(user.mfa_secret, code):
        raise MfaInvalid()
    user.mfa_enrolled = True
    await audit_service.write(db, actor_id=user.id, action="user.mfa_enrolled", entity="user", entity_id=user.id)


async def change_role(
    db: AsyncSession, *, target_user_id: uuid.UUID, new_role: Role, actor_id: uuid.UUID, actor_role: Role
) -> User:
    user = await user_repo.get_by_id(db, target_user_id)
    if user is None:
        raise NotFound("User")
    role_row = await user_repo.get_role(db, target_user_id)
    if role_row is None:
        raise NotFound("User role")

    old_role = role_row.role
    if not can_manage_account(actor_role, old_role) or not can_assign_roles(actor_role, old_role, new_role):
        raise Forbidden("Only a Platform Admin can create, change or remove a Platform Admin.")
    if old_role == Role.PLATFORM_ADMIN and new_role != Role.PLATFORM_ADMIN:
        if await user_repo.count_active_platform_admins(db, excluding_user_id=target_user_id) < 1:
            raise LastPlatformAdminGuard()
    if old_role == Role.OWNER and new_role != Role.OWNER:
        remaining = await user_repo.count_active_owners(db, excluding_user_id=target_user_id)
        if remaining < 1:
            raise LastOwnerGuard()

    role_row.role = new_role
    role_row.assigned_by = actor_id
    role_row.assigned_at = datetime.now(UTC)

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="user.role_changed",
        entity="user",
        entity_id=target_user_id,
        before={"role": old_role.value},
        after={"role": new_role.value},
    )
    return user


async def deactivate(
    db: AsyncSession, *, target_user_id: uuid.UUID, actor_id: uuid.UUID, actor_role: Role
) -> User:
    user = await user_repo.get_by_id(db, target_user_id)
    if user is None:
        raise NotFound("User")
    role_row = await user_repo.get_role(db, target_user_id)
    if role_row is not None and not can_manage_account(actor_role, role_row.role):
        raise Forbidden("Only a Platform Admin can deactivate a Platform Admin.")
    if role_row is not None and role_row.role == Role.PLATFORM_ADMIN:
        if await user_repo.count_active_platform_admins(db, excluding_user_id=target_user_id) < 1:
            raise LastPlatformAdminGuard()

    if role_row is not None and role_row.role == Role.OWNER:
        remaining = await user_repo.count_active_owners(db, excluding_user_id=target_user_id)
        if remaining < 1:
            raise LastOwnerGuard()

    before_status = user.invite_status.value
    user.invite_status = InviteStatus.DEACTIVATED
    await refresh_token_repo.revoke_all_for_user(db, user_id=user.id, revoked_at=datetime.now(UTC))

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="user.deactivated",
        entity="user",
        entity_id=target_user_id,
        before={"invite_status": before_status},
        after={"invite_status": InviteStatus.DEACTIVATED.value},
    )
    return user


async def reactivate(
    db: AsyncSession, *, target_user_id: uuid.UUID, actor_id: uuid.UUID, actor_role: Role
) -> User:
    user = await user_repo.get_by_id(db, target_user_id)
    if user is None:
        raise NotFound("User")
    role_row = await user_repo.get_role(db, target_user_id)
    if role_row is not None and not can_manage_account(actor_role, role_row.role):
        raise Forbidden("Only a Platform Admin can reactivate a Platform Admin.")
    if user.invite_status != InviteStatus.DEACTIVATED:
        raise Conflict("NOT_DEACTIVATED", "User is not currently deactivated.")

    before_status = user.invite_status.value
    user.invite_status = InviteStatus.ACTIVE

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="user.reactivated",
        entity="user",
        entity_id=target_user_id,
        before={"invite_status": before_status},
        after={"invite_status": InviteStatus.ACTIVE.value},
    )
    return user


async def set_temporary_password(
    db: AsyncSession,
    *,
    target_user_id: uuid.UUID,
    temporary_password: str,
    actor_id: uuid.UUID,
    actor_role: Role,
) -> User:
    """An admin sets a one-off password for a user who cannot sign in (there
    is no email service to send a reset link). The user is signed out
    everywhere and must choose their own password at next login: their
    sessions carry a `pwc` claim until they do (api/deps.get_current_user).
    The temporary password itself is never stored anywhere but as a hash, and
    never appears in the audit log."""
    user = await user_repo.get_by_id(db, target_user_id)
    if user is None:
        raise NotFound("User")
    role_row = await user_repo.get_role(db, target_user_id)
    if role_row is None:
        raise NotFound("User role")
    if target_user_id == actor_id:
        raise Conflict("USE_CHANGE_PASSWORD", "Change your own password from your profile instead.")
    if not can_manage_account(actor_role, role_row.role) or not can_set_temporary_password(actor_role, role_row.role):
        raise Forbidden("You do not have permission to reset this account's password.")
    if user.invite_status != InviteStatus.ACTIVE:
        raise Conflict(
            "USER_NOT_ACTIVE",
            "Only an active user can be given a temporary password "
            "(a pending user should use their invite link; a deactivated user must be reactivated first).",
        )

    await _enforce_password_policy(temporary_password)

    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    await refresh_token_repo.revoke_all_for_user(db, user_id=user.id, revoked_at=datetime.now(UTC))

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="user.temporary_password_set",
        entity="user",
        entity_id=target_user_id,
        after={"must_change_password": True},
    )
    return user
