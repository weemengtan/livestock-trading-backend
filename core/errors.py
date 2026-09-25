from typing import Any


class AppError(Exception):
    """Raise this anywhere in services/routes for a domain-level failure.
    A single exception handler in main.py turns it into §9's envelope:
    { "error": { "code", "message", "details" } }.

    `retriable` defaults to False: every AppError is a structured, named
    domain rejection (invalid input, a business rule not met, a resource
    that doesn't exist) — deterministic given the same payload, so retrying
    it unchanged will never succeed. This matters specifically for the
    offline-sync per-item paths (services/buy_entry_service.py::bulk_sync,
    services/market_observation_service.py::bulk_sync): they read this
    flag to decide whether a failed queued item goes back to "queued" for
    the next auto-retry, or to a terminal "failed" state that stops
    retrying and asks the buyer to fix or discard it. Only an unstructured,
    unexpected exception (not an AppError at all — a real infra/network
    hiccup) should be treated as retriable; nothing here overrides that
    default, on purpose."""

    def __init__(
        self, code: str, message: str, status_code: int = 400, details: Any = None, *, retriable: bool = False
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.retriable = retriable
        super().__init__(message)


class InvalidCredentials(AppError):
    def __init__(self) -> None:
        super().__init__("INVALID_CREDENTIALS", "Incorrect email or password.", status_code=401)


class MfaRequired(AppError):
    def __init__(self) -> None:
        super().__init__("MFA_REQUIRED", "A valid TOTP code is required.", status_code=401)


class MfaInvalid(AppError):
    def __init__(self) -> None:
        super().__init__("MFA_INVALID", "The TOTP code is incorrect or expired.", status_code=401)


class AccountNotActive(AppError):
    def __init__(self, status: str) -> None:
        super().__init__("ACCOUNT_NOT_ACTIVE", f"Account is {status}, not active.", status_code=403)


class RateLimited(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "RATE_LIMITED",
            "Too many attempts. Try again shortly.",
            status_code=429,
            details={"retry_after_seconds": retry_after_seconds},
        )


class InvalidToken(AppError):
    def __init__(self, message: str = "Invalid or expired token.") -> None:
        super().__init__("INVALID_TOKEN", message, status_code=401)


class TokenReused(AppError):
    def __init__(self) -> None:
        super().__init__(
            "REFRESH_TOKEN_REUSED",
            "This session was revoked because a refresh token was reused. Please log in again.",
            status_code=401,
        )


class Forbidden(AppError):
    def __init__(self, message: str = "You do not have permission to do this.") -> None:
        super().__init__("FORBIDDEN", message, status_code=403)


class InvalidCurrentPassword(AppError):
    """Deliberately not a 401: a wrong *password* is not an expired *session*,
    and clients treat 401 as "refresh the token and retry"."""

    def __init__(self) -> None:
        super().__init__("INVALID_CURRENT_PASSWORD", "Current password is incorrect.", status_code=400)


class PasswordChangeRequired(AppError):
    """The account is on a temporary password: every route except the
    change-password one refuses the session until it has been changed."""

    def __init__(self) -> None:
        super().__init__(
            "PASSWORD_CHANGE_REQUIRED",
            "You must change your temporary password before continuing.",
            status_code=403,
        )


class NotFound(AppError):
    def __init__(self, entity: str) -> None:
        super().__init__("NOT_FOUND", f"{entity} not found.", status_code=404)


class Conflict(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=409)


class LastOwnerGuard(AppError):
    def __init__(self) -> None:
        super().__init__(
            "LAST_OWNER_GUARD",
            "Cannot deactivate the platform's last remaining active OWNER.",
            status_code=409,
        )


class LastPlatformAdminGuard(AppError):
    def __init__(self) -> None:
        super().__init__(
            "LAST_PLATFORM_ADMIN_GUARD",
            "Cannot demote or deactivate the last remaining active PLATFORM_ADMIN.",
            status_code=409,
        )


class PasswordPolicyViolation(AppError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(
            "PASSWORD_POLICY_VIOLATION",
            "Password does not meet the minimum policy.",
            status_code=422,
            details={"violations": violations},
        )


class BreachedPassword(AppError):
    def __init__(self) -> None:
        super().__init__(
            "BREACHED_PASSWORD",
            "This password has appeared in a known data breach. Choose a different one.",
            status_code=422,
        )


class PreviewExpired(AppError):
    def __init__(self) -> None:
        super().__init__(
            "PREVIEW_EXPIRED",
            "This upload preview has expired or was not found. Please re-upload the file.",
            status_code=410,
        )


class SnapshotNotCalculated(AppError):
    def __init__(self) -> None:
        super().__init__(
            "SNAPSHOT_NOT_CALCULATED",
            "This snapshot has not been calculated yet.",
            status_code=409,
        )


class PublicationBlocked(AppError):
    def __init__(self, blocked_line_ids: list[str]) -> None:
        super().__init__(
            "PUBLICATION_BLOCKED",
            "One or more active lines have a BLOCK issue — publication is refused (§5.7).",
            status_code=409,
            details={"blocked_order_line_ids": blocked_line_ids},
        )


class IssuesNotAcknowledged(AppError):
    def __init__(self, issue_ids: list[str]) -> None:
        super().__init__(
            "ISSUES_NOT_ACKNOWLEDGED",
            "Every WARN/CORRECTION issue on an active line must be acknowledged before publishing.",
            status_code=409,
            details={"unacknowledged_issue_ids": issue_ids},
        )


class NothingToPublish(AppError):
    def __init__(self) -> None:
        super().__init__(
            "NOTHING_TO_PUBLISH",
            "No active line in this snapshot has a computed DNBP to publish.",
            status_code=409,
        )


class TicketInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(
            "TICKET_INVALID", "This connection ticket is invalid, expired, or already used.", status_code=401
        )


class AbattoirOwnedTable(AppError):
    """§9.8: `POST /reference-data/versions` MUST reject any attempt to
    write an abattoir-owned table (§6.1-6.3, §6.6)."""

    def __init__(self, table_key: str) -> None:
        super().__init__(
            "ABATTOIR_OWNED_TABLE",
            f"'{table_key}' is maintained by the abattoir and received with each submission — it cannot be "
            "edited here.",
            status_code=403,
        )


class ImpactPreviewRequired(AppError):
    """§6.5, §9.8, §19: activation is refused unless the impact preview was
    fetched for this exact version first."""

    def __init__(self) -> None:
        super().__init__(
            "IMPACT_PREVIEW_REQUIRED",
            "Fetch the impact preview for this version before activating it.",
            status_code=409,
        )


class RegistryCodeExists(AppError):
    def __init__(self, code: str) -> None:
        super().__init__(
            "REGISTRY_CODE_EXISTS",
            f"'{code}' is already registered.",
            status_code=409,
        )


class NothingToInstruct(AppError):
    def __init__(self) -> None:
        super().__init__(
            "NOTHING_TO_INSTRUCT",
            "No active line in this snapshot has a computed DNBP to instruct.",
            status_code=409,
        )


class InvalidInstructionTransition(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_INSTRUCTION_TRANSITION", message, status_code=409)


class InstructionNotApproved(AppError):
    def __init__(self) -> None:
        super().__init__(
            "INSTRUCTION_NOT_APPROVED",
            "This instruction must be approved (by an OWNER) before it can be issued.",
            status_code=409,
        )


class FillsNotAllowed(AppError):
    def __init__(self) -> None:
        super().__init__(
            "FILLS_NOT_ALLOWED",
            "Reconciliation fills can only be added or removed while the instruction is ISSUED or ACKNOWLEDGED.",
            status_code=409,
        )


class NoActiveReferenceDataError(AppError):
    """Raised if reference_data_versions has no active row — should never
    happen past the Phase 3b migration, which seeds and activates one, but
    the engine must never silently fall back to the seed file once the DB
    is the source of truth: that would let a stale file value outlive an
    admin's deliberate change. A genuine server-side misconfiguration, not
    something the caller did wrong — hence 500, not 4xx."""

    def __init__(self) -> None:
        super().__init__(
            "NO_ACTIVE_REFERENCE_DATA",
            "No active reference data version exists yet — the DNBP model needs to be configured before this "
            "can be used.",
            status_code=500,
        )


class ReferenceDataIncomplete(AppError):
    """The active reference-data version lacks a value the system needs.
    Never defaulted or guessed: the missing key is named so it can be added
    in a new version."""

    def __init__(self, missing: str) -> None:
        super().__init__(
            "REFERENCE_DATA_INCOMPLETE",
            f"The active reference data version has no value for {missing}. Add it in a new version.",
            status_code=500,
            details={"missing": missing},
        )


class FourEyesRequired(AppError):
    def __init__(self) -> None:
        super().__init__(
            "FOUR_EYES_REQUIRED",
            "A reference data version must be activated by someone other than the person who created it.",
            status_code=403,
        )


class IngestionRejected(AppError):
    """Wraps domain.ingestion.errors.IngestionContractError at the service
    boundary — the domain layer stays framework-agnostic (never imports
    core/errors itself), so translation happens here, not there. The
    message is written for the business user; `details` carries what the
    UI needs (e.g. the tabs that were found)."""

    def __init__(self, error) -> None:
        super().__init__(error.code.value, error.message, status_code=422, details=error.details)


class ImplausibleEntry(AppError):
    """domain.buyer.entry_bounds's sanity net — the entry's head_count,
    price_per_head, or weight_kg is outside a deliberately loose plausible
    range, almost certainly a data-entry error (extra zeros, wrong unit)
    rather than a genuine trade. `details.violations` lists every bound
    that was missed, not just the first."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__(
            "IMPLAUSIBLE_ENTRY",
            "This entry looks implausible — check the numbers before saving.",
            status_code=422,
            details={"violations": violations},
        )


class ModelFourEyesRequired(AppError):
    """Same rule as FourEyesRequired (§6.5), worded for DNBP models: the
    person who created a model cannot be the one who approves it."""

    def __init__(self) -> None:
        super().__init__(
            "FOUR_EYES_REQUIRED",
            "A DNBP model must be approved by someone other than the person who created it.",
            status_code=403,
        )


class ActivationDateNotInFuture(AppError):
    def __init__(self, earliest_date: str) -> None:
        super().__init__(
            "ACTIVATION_DATE_NOT_IN_FUTURE",
            f"A model can only be scheduled for a future date (Singapore time). The earliest is {earliest_date}.",
            status_code=422,
        )


class ActivationDateTaken(AppError):
    def __init__(self, other_model_name: str) -> None:
        super().__init__(
            "ACTIVATION_DATE_TAKEN",
            f"Model '{other_model_name}' is already scheduled to go live on that date. Only one model can switch "
            "on at a given moment — pick a different date or cancel the other one.",
            status_code=409,
        )


class ModelAlreadyLive(AppError):
    """A model that has gone live (even if since replaced) has priced real
    orders and is history — it can be neither rescheduled nor cancelled."""

    def __init__(self) -> None:
        super().__init__(
            "MODEL_ALREADY_LIVE",
            "This model has already gone live, so it can no longer be changed or cancelled. Create a new model "
            "instead.",
            status_code=409,
        )


class SnapshotFrozen(AppError):
    """A superseded snapshot's price is no longer current; recalculating it
    would only resurrect an out-of-date computation."""

    def __init__(self) -> None:
        super().__init__(
            "SNAPSHOT_FROZEN",
            "This snapshot has been superseded by a later publication and is frozen. Upload the order file "
            "again to reprice it.",
            status_code=409,
        )


class PublishedUnderOlderModel(AppError):
    """Recalculating a published snapshot under the same model reproduces the
    same numbers, so that stays allowed (review-and-republish). Under a
    *different* live model it would overwrite the workings behind prices
    buyers already received."""

    def __init__(self, live_model_name: str, calculated_under: list[str]) -> None:
        super().__init__(
            "PUBLISHED_UNDER_OLDER_MODEL",
            f"This snapshot was published under {', '.join(calculated_under)}, but DNBP model '{live_model_name}' "
            "is now live. Recalculating would overwrite the workings behind the prices buyers already received — "
            "upload the order file again to reprice it under the current model.",
            status_code=409,
            details={"live_model": live_model_name, "calculated_under": calculated_under},
        )


class CalculatedUnderOlderModel(AppError):
    def __init__(self, live_model_name: str, calculated_under: list[str]) -> None:
        super().__init__(
            "CALCULATED_UNDER_OLDER_MODEL",
            f"This snapshot was calculated under {', '.join(calculated_under)}, but DNBP model "
            f"'{live_model_name}' is now live. Recalculate before publishing.",
            status_code=409,
            details={"live_model": live_model_name, "calculated_under": calculated_under},
        )
