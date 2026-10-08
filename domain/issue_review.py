"""Two-tier review of WARN/CORRECTION issues — pure rules, no database.

An ACCOUNTANT's decision is a RECOMMENDATION (advisory; clears nothing). An
OWNER's decision is FINAL: only an OWNER APPROVE acknowledges the issue, which
is what lets a publication proceed (services/publication_service.py). An OWNER
REJECT sends the line back to be fixed and recalculated.

Every decision carries a remark. An APPROVE of a pricing concern (money at
risk) also carries a reason code from a fixed list, so the trail is reportable
rather than free text alone.
"""

import enum
import uuid
from datetime import datetime, timedelta


class ReviewDecision(enum.StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ReviewTier(enum.StrEnum):
    RECOMMENDATION = "RECOMMENDATION"
    FINAL = "FINAL"


REASON_CODES: dict[str, str] = {
    "COST_CONFIRMED": "Cost confirmed with Peter",
    "MARKET_MOVE": "Market move expected",
    "CUSTOMER_RELATIONSHIP": "Customer relationship",
    "ONE_OFF_DEAL": "One-off / volume deal",
    "OTHER": "Other (explain in remark)",
}

# Mirrors the "Pricing" grouping on the Publish screen: the issues where an
# approval accepts a direct financial exposure.
PRICING_ISSUE_CODES = frozenset({"MARGIN_BUFFER_ERODED", "NEGATIVE_MARGIN", "DNBP_OUTLIER", "LARGE_BENCHMARK_GAP"})

MIN_REMARK_LENGTH = 3
MAX_REMARK_LENGTH = 1000

# One trading week. An approval carried onto a later snapshot lapses after this.
APPROVAL_VALIDITY = timedelta(days=7)


def check_review_input(
    issue_code: str, decision: ReviewDecision, reason_code: str | None, remark: str | None
) -> str | None:
    """None if the decision is acceptable, else the reason it is not."""
    if remark is None or len(remark.strip()) < MIN_REMARK_LENGTH:
        return "A remark is required for every approve or reject decision."
    if len(remark.strip()) > MAX_REMARK_LENGTH:
        return f"The remark must be at most {MAX_REMARK_LENGTH} characters."
    if reason_code is not None and reason_code not in REASON_CODES:
        return f"Unknown reason code '{reason_code}'."
    if decision is ReviewDecision.APPROVE and issue_code in PRICING_ISSUE_CODES and reason_code is None:
        return "A reason code is required to approve a pricing warning."
    return None


def approval_expires_at(approved_at: datetime) -> datetime:
    return approved_at + APPROVAL_VALIDITY


def is_approval_current(
    approved_at: datetime,
    approved_model_id: uuid.UUID | None,
    current_model_id: uuid.UUID | None,
    now: datetime,
) -> bool:
    """Whether an earlier approval may still be carried onto a new snapshot:
    not past its validity window, and given under the same DNBP model now in
    force. An approval with no recorded model (given before models were
    tracked here) is judged on the window alone."""
    if now >= approval_expires_at(approved_at):
        return False
    if approved_model_id is not None and current_model_id is not None and approved_model_id != current_model_id:
        return False
    return True
