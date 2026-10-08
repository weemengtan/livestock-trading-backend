"""Two-tier review of WARN/CORRECTION issues (rules: domain/issue_review.py).

The caller's role decides the tier — never the request body: an OWNER's
decision is FINAL, an ACCOUNTANT's is a RECOMMENDATION. Only an OWNER APPROVE
stamps the issue acknowledged (what the publication gate checks) and records
the durable, carry-forward decision. Every decision, either tier, writes one
audit_log entry per issue, against the snapshot, carrying who decided, in what
role, why, and the figures the decider was looking at.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AppError, Conflict
from core.permissions import role_satisfies
from domain.engine.issues import Severity
from domain.issue_review import ReviewDecision, ReviewTier, approval_expires_at, check_review_input
from models.enums import Role, SnapshotStatus
from models.order_line import OrderLine
from models.order_snapshot import OrderSnapshot
from models.validation_issue import ValidationIssueRecord
from repositories import order_workings as order_workings_repo
from services import audit_service, issue_acknowledgment_service


def tier_for_role(role: Role) -> ReviewTier:
    return ReviewTier.FINAL if role_satisfies(role, (Role.OWNER,)) else ReviewTier.RECOMMENDATION


async def _figures(db: AsyncSession, line: OrderLine) -> tuple[dict, uuid.UUID | None]:
    """What the decider was looking at, frozen into the audit entry, and the
    DNBP model those figures came from."""
    workings = await order_workings_repo.get_by_order_line_id(db, line.id)
    if workings is None:
        return {}, None

    def text(value: object) -> str | None:
        return None if value is None else str(value)

    return (
        {
            "dnbp_per_kg": text(workings.bing_dnbp),
            "expected_livestock_cost_per_kg": text(line.expected_livestock_cost_per_kg),
            "profit_on_dnbp": text(workings.profit_on_bing_dnbp),
            "diff_vs_peter": text(workings.diff_vs_peter),
            "diff_vs_benchmark": text(workings.diff_vs_benchmark),
            "dnbp_model": workings.ref_data_version,
            "dnbp_model_id": text(workings.ref_data_version_id),
        },
        workings.ref_data_version_id,
    )


async def review(
    db: AsyncSession,
    *,
    snapshot: OrderSnapshot,
    issues: list[ValidationIssueRecord],
    lines: dict[uuid.UUID, OrderLine],
    actor_id: uuid.UUID,
    actor_role: Role,
    decision: ReviewDecision,
    reason_code: str | None,
    remark: str,
) -> ReviewTier:
    """All-or-nothing: every issue is validated before any is touched. Caller
    owns the commit."""
    if snapshot.status != SnapshotStatus.CALCULATED:
        raise Conflict("SNAPSHOT_NOT_REVIEWABLE", "Only a calculated, unpublished snapshot can be reviewed.")
    tier = tier_for_role(actor_role)

    for issue in issues:
        if issue.severity is Severity.BLOCK:
            raise Conflict(
                "CANNOT_ACKNOWLEDGE_BLOCK",
                "A BLOCK issue cannot be approved away — it can only be resolved by a corrected submission.",
            )
        if issue.severity not in (Severity.WARN, Severity.CORRECTION):
            raise Conflict("ISSUE_NOT_REVIEWABLE", "Only warnings can be reviewed.")
        if issue.acknowledged_at is not None:
            raise Conflict("ISSUE_ALREADY_APPROVED", "This warning has already been approved.")
        problem = check_review_input(issue.code, decision, reason_code, remark)
        if problem is not None:
            raise AppError("INVALID_REVIEW", problem, status_code=422)

    now = datetime.now(UTC)
    clean_remark = remark.strip()
    self_approved = actor_id == snapshot.uploaded_by

    for issue in issues:
        line = lines[issue.order_line_id]
        figures, model_id = await _figures(db, line)
        overrides = (
            tier is ReviewTier.FINAL
            and issue.recommendation_decision is not None
            and issue.recommendation_decision != decision.value
        )

        if tier is ReviewTier.RECOMMENDATION:
            issue.recommendation_decision = decision.value
            issue.recommendation_reason_code = reason_code
            issue.recommendation_remark = clean_remark
            issue.recommended_by = actor_id
            issue.recommended_at = now
            action = "issue_review.recommended"
        elif decision is ReviewDecision.APPROVE:
            issue.acknowledged_by = actor_id
            issue.acknowledged_at = now
            issue.approval_reason_code = reason_code
            issue.approval_remark = clean_remark
            issue.approval_expires_at = approval_expires_at(now)
            issue.rejected_by = issue.rejected_at = issue.rejection_remark = None
            await issue_acknowledgment_service.record(
                db,
                line,
                org_id=snapshot.org_id,
                code=issue.code,
                column_ref=issue.column_ref,
                acknowledged_by=actor_id,
                snapshot_id=snapshot.id,
                reason_code=reason_code,
                remark=clean_remark,
                dnbp_model_id=model_id,
            )
            action = "issue_review.approved"
        else:
            issue.rejected_by = actor_id
            issue.rejected_at = now
            issue.rejection_remark = clean_remark
            action = "issue_review.rejected"

        await audit_service.write(
            db,
            actor_id=actor_id,
            action=action,
            entity="order_snapshot",
            entity_id=snapshot.id,
            after={
                "issue_id": str(issue.id),
                "order_line_id": str(line.id),
                "line_no": line.line_no,
                "contract_no": line.contract_no,
                "issue_code": issue.code,
                "severity": issue.severity.value,
                "tier": tier.value,
                "decision": decision.value,
                "reason_code": reason_code,
                "remark": clean_remark,
                "actor_role": actor_role.value,
                "self_approved": self_approved,
                "overrides_recommendation": overrides,
                "recommendation_at_decision": issue.recommendation_decision,
                "figures": figures,
            },
        )
    return tier
