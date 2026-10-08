import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, redis_dep, require_role
from core.db import get_db
from core.errors import NotFound, SnapshotFrozen
from domain.issue_review import ReviewDecision
from models.audit_log import AuditLog
from models.enums import Role, SnapshotStatus
from repositories import order_lines as order_lines_repo
from repositories import order_snapshots as order_snapshots_repo
from repositories import order_workings as order_workings_repo
from repositories import users as users_repo
from repositories import validation_issues as validation_issues_repo
from schemas.dnbp_models import ModelStampResponse, SnapshotModelStatusResponse
from schemas.order_lines import OrderLineResponse, OrderWorkingsResponse
from schemas.snapshots import (
    CalculateResponse,
    CommitSnapshotRequest,
    IngestionContractResponse,
    IssueRecommendation,
    IssueRejection,
    IssueResponse,
    ReviewAuditEntryResponse,
    ReviewIssuesRequest,
    SnapshotResponse,
    UploadPreviewResponse,
)
from services import (
    calculate_service,
    dnbp_model_service,
    ingestion_service,
    issue_review_service,
)
from services.ingestion_contract_service import get_active_contract

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

# §9.2 — snapshot/ingestion routes are OWNER + ACCOUNTANT only. Both may
# upload (§11.2) — the abattoir's email may land with either of them.
_trading_console = require_role(Role.OWNER, Role.ACCOUNTANT)


def _to_line_response(line) -> OrderLineResponse:
    return OrderLineResponse(
        id=line.id,
        snapshot_id=line.snapshot_id,
        line_no=line.line_no,
        contract_no=line.contract_no,
        customer_name=line.customer_name,
        species=line.species,
        loadout_date=line.loadout_date,
        qty_kg=line.qty_kg,
        avg_price_aud=line.avg_price_aud,
        amount_aud=line.amount_aud,
        product_type=line.product_type,
        incoterm=line.incoterm.value if line.incoterm else None,
        nrv_per_kg=line.nrv_per_kg,
        expected_livestock_cost_per_kg=line.expected_livestock_cost_per_kg,
        pack_cost_ph=line.pack_cost_ph,
        offal_return_ph=line.offal_return_ph,
        skin_return_ph=line.skin_return_ph,
        avg_weight_kg=line.avg_weight_kg,
        mom_ph=line.mom_ph,
        deposit_received=line.deposit_received,
        comments=line.comments,
        dnbp_benchmark=line.dnbp_benchmark,
        benchmark_method=line.benchmark_method.value if line.benchmark_method else None,
        estimated_heads=line.estimated_heads,
        total_livestock_cost=line.total_livestock_cost,
        value_sources=line.value_sources,
    )


async def _get_owned_snapshot(db: AsyncSession, snapshot_id: uuid.UUID, org_id: uuid.UUID):
    snapshot = await order_snapshots_repo.get_by_id(db, snapshot_id)
    if snapshot is None or snapshot.org_id != org_id:
        raise NotFound("Snapshot")
    return snapshot


@router.get("/ingestion-contract", response_model=IngestionContractResponse)
async def get_ingestion_contract(
    current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> IngestionContractResponse:
    # Declared before the `/{snapshot_id}` route so it is not captured as an id.
    contract = await get_active_contract(db)
    return IngestionContractResponse(version=contract.version, required_sheet_name=contract.required_sheet_name)


@router.post("/upload", response_model=UploadPreviewResponse)
async def upload(
    file: UploadFile = File(...),
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(redis_dep),
) -> UploadPreviewResponse:
    file_bytes = await file.read()
    preview = await ingestion_service.create_upload_preview(
        db, redis, org_id=current.org_id, file_bytes=file_bytes, filename=file.filename or "upload.xlsx"
    )
    return UploadPreviewResponse(**preview)


@router.post("", response_model=SnapshotResponse, status_code=201)
async def commit(
    body: CommitSnapshotRequest,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(redis_dep),
) -> SnapshotResponse:
    snapshot = await ingestion_service.commit_snapshot(
        db, redis, org_id=current.org_id, uploaded_by=current.user_id, preview_id=body.preview_id
    )
    await db.commit()
    return SnapshotResponse.model_validate(snapshot)


@router.get("", response_model=list[SnapshotResponse])
async def list_snapshots(
    current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> list[SnapshotResponse]:
    snapshots = await order_snapshots_repo.list_for_org(db, current.org_id)
    return [SnapshotResponse.model_validate(s) for s in snapshots]


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot(
    snapshot_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> SnapshotResponse:
    snapshot = await _get_owned_snapshot(db, snapshot_id, current.org_id)
    return SnapshotResponse.model_validate(snapshot)


@router.get("/{snapshot_id}/diff/{other_id}")
async def diff(
    snapshot_id: uuid.UUID,
    other_id: uuid.UUID,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    await _get_owned_snapshot(db, other_id, current.org_id)
    return await ingestion_service.diff_snapshots(db, other_id, snapshot_id)


@router.get("/{snapshot_id}/lines", response_model=list[OrderLineResponse])
async def list_lines(
    snapshot_id: uuid.UUID,
    species: str | None = Query(default=None),
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
) -> list[OrderLineResponse]:
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    lines = await order_lines_repo.list_by_snapshot(db, snapshot_id, species=species)
    return [_to_line_response(line) for line in lines]


@router.get("/{snapshot_id}/workings", response_model=list[OrderWorkingsResponse])
async def list_workings(
    snapshot_id: uuid.UUID,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
) -> list[OrderWorkingsResponse]:
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    workings = await order_workings_repo.list_by_snapshot_id(db, snapshot_id)
    return [OrderWorkingsResponse.model_validate(w) for w in workings]


@router.post("/{snapshot_id}/calculate", response_model=CalculateResponse)
async def calculate(
    snapshot_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> CalculateResponse:
    snapshot = await _get_owned_snapshot(db, snapshot_id, current.org_id)
    # A published snapshot's workings are the evidence behind prices buyers
    # already received. Recalculating under the *same* model reproduces them
    # exactly (review-and-republish, the Publish panel's own flow), but under
    # a different live model it would overwrite that evidence — refused; the
    # order file is uploaded again to reprice. A superseded snapshot is
    # simply frozen.
    if snapshot.status is SnapshotStatus.SUPERSEDED:
        raise SnapshotFrozen()
    if snapshot.status is SnapshotStatus.PUBLISHED:
        await dnbp_model_service.assert_published_snapshot_on_live_model(db, snapshot.id)
    summary = await calculate_service.calculate_snapshot(db, snapshot, actor_id=current.user_id)
    snapshot.status = SnapshotStatus.CALCULATED
    await db.commit()
    return CalculateResponse(**summary)


@router.get("/{snapshot_id}/model-status", response_model=SnapshotModelStatusResponse)
async def model_status(
    snapshot_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> SnapshotModelStatusResponse:
    """Which DNBP model this snapshot was calculated under versus the one
    live now — drives the workbench's "recalculate before publishing" banner."""
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    status = await dnbp_model_service.snapshot_model_status(db, snapshot_id)
    return SnapshotModelStatusResponse(
        live_model_id=status.live_model_id,
        live_model_name=status.live_model_name,
        calculated_under=[
            ModelStampResponse(model_id=s.model_id, name=s.name, line_count=s.line_count)
            for s in status.calculated_under
        ],
        stale=status.stale,
    )


def _to_issue_response(issue, emails: dict[uuid.UUID, str]) -> IssueResponse:
    recommendation = None
    if issue.recommended_by is not None and issue.recommendation_decision is not None:
        recommendation = IssueRecommendation(
            decision=issue.recommendation_decision,
            reason_code=issue.recommendation_reason_code,
            remark=issue.recommendation_remark,
            by=issue.recommended_by,
            by_email=emails.get(issue.recommended_by),
            at=issue.recommended_at,
        )
    rejection = None
    if issue.rejected_by is not None:
        rejection = IssueRejection(
            remark=issue.rejection_remark,
            by=issue.rejected_by,
            by_email=emails.get(issue.rejected_by),
            at=issue.rejected_at,
        )
    return IssueResponse(
        id=issue.id,
        order_line_id=issue.order_line_id,
        code=issue.code,
        severity=issue.severity.value,
        message=issue.message,
        column_ref=issue.column_ref,
        acknowledged_by=issue.acknowledged_by,
        acknowledged_by_email=emails.get(issue.acknowledged_by) if issue.acknowledged_by else None,
        acknowledged_at=issue.acknowledged_at,
        carried_forward=issue.carried_forward,
        approval_reason_code=issue.approval_reason_code,
        approval_remark=issue.approval_remark,
        approval_expires_at=issue.approval_expires_at,
        recommendation=recommendation,
        rejection=rejection,
    )


async def _issue_responses(db: AsyncSession, issues) -> list[IssueResponse]:
    user_ids = {
        user_id
        for issue in issues
        for user_id in (issue.acknowledged_by, issue.recommended_by, issue.rejected_by)
        if user_id is not None
    }
    emails = await users_repo.emails_by_id(db, user_ids)
    return [_to_issue_response(issue, emails) for issue in issues]


@router.get("/{snapshot_id}/issues", response_model=list[IssueResponse])
async def list_issues(
    snapshot_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> list[IssueResponse]:
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    return await _issue_responses(db, await validation_issues_repo.list_by_snapshot(db, snapshot_id))


@router.post("/{snapshot_id}/issues/review", response_model=list[IssueResponse])
async def review_issues(
    snapshot_id: uuid.UUID,
    body: ReviewIssuesRequest,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
) -> list[IssueResponse]:
    """§5.7's publication gate, as a two-tier review. An ACCOUNTANT's decision
    is a recommendation; an OWNER's is final, and only an OWNER approval
    acknowledges the warning so a publication can proceed (see
    services/issue_review_service.py). One request and one transaction for the
    whole selection — a full snapshot can carry hundreds of issues, and one
    call each would blow past the general API rate limit. All-or-nothing."""
    snapshot = await _get_owned_snapshot(db, snapshot_id, current.org_id)
    issue_ids = list(dict.fromkeys(body.issue_ids))
    issues = await validation_issues_repo.list_by_ids(db, issue_ids)
    if len(issues) != len(issue_ids):
        raise NotFound("Issue")
    lines = {line.id: line for line in await order_lines_repo.list_by_ids(db, list({i.order_line_id for i in issues}))}
    for issue in issues:
        line = lines.get(issue.order_line_id)
        if line is None or line.snapshot_id != snapshot_id:
            raise NotFound("Issue")

    await issue_review_service.review(
        db,
        snapshot=snapshot,
        issues=issues,
        lines=lines,
        actor_id=current.user_id,
        actor_role=current.role,
        decision=ReviewDecision(body.decision),
        reason_code=body.reason_code,
        remark=body.remark,
    )
    await db.commit()
    return await _issue_responses(db, issues)


@router.get("/{snapshot_id}/review-audit", response_model=list[ReviewAuditEntryResponse])
async def review_audit(
    snapshot_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
) -> list[ReviewAuditEntryResponse]:
    """Every recommendation, approval and rejection made on this snapshot's
    warnings, newest first — read straight from the tamper-evident audit_log."""
    await _get_owned_snapshot(db, snapshot_id, current.org_id)
    entries = (
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity == "order_snapshot",
                    AuditLog.entity_id == snapshot_id,
                    AuditLog.action.like("issue_review.%"),
                )
                .order_by(AuditLog.seq.desc())
            )
        )
        .scalars()
        .all()
    )
    emails = await users_repo.emails_by_id(db, {e.actor_id for e in entries if e.actor_id is not None})
    return [
        ReviewAuditEntryResponse(
            id=e.id,
            action=e.action,
            at=e.at,
            actor_email=emails.get(e.actor_id) if e.actor_id is not None else None,
            after=e.after,
        )
        for e in entries
    ]

