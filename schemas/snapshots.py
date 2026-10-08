import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.enums import SnapshotStatus


class CommitSnapshotRequest(BaseModel):
    preview_id: str


class DuplicateOfCurrent(BaseModel):
    """Populated when the uploaded file's content is byte-identical to the
    org's current snapshot — surfaced so a human can decide whether this is
    a deliberate resubmission or someone else already uploaded it (§11.2:
    both Owner and Accountant can upload, so this is a real scenario, not
    just a double-click)."""

    snapshot_id: uuid.UUID
    uploaded_by_email: str
    uploaded_at: datetime


class IngestionContractResponse(BaseModel):
    """What an uploaded workbook must contain — served so the UI can check a
    file before upload without hard-coding the tab name a second time."""

    version: str
    required_sheet_name: str


class UploadPreviewResponse(BaseModel):
    preview_id: str
    detected_layout: dict[str, Any]
    active_count: int
    diff: dict[str, Any]
    duplicate_of_current: DuplicateOfCurrent | None = None


class SnapshotResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    uploaded_by: uuid.UUID
    source_filename: str
    source_sha256: str
    detected_layout: dict[str, Any]
    parser_version: str
    status: SnapshotStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class CalculateResponse(BaseModel):
    active_lines_computed: int
    blocked_issues: int
    correction_issues: int
    warnings: int
    correction_requests_auto_resolved: int


MAX_REVIEW_BATCH = 1000


class ReviewIssuesRequest(BaseModel):
    """One decision applied to a batch of issues. The caller's role — not this
    body — decides whether it is a recommendation or the final call."""

    issue_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_REVIEW_BATCH)
    decision: Literal["APPROVE", "REJECT"]
    reason_code: str | None = None
    remark: str = Field(max_length=1000)


class IssueRecommendation(BaseModel):
    decision: str
    reason_code: str | None
    remark: str | None
    by: uuid.UUID
    by_email: str | None
    at: datetime


class IssueRejection(BaseModel):
    remark: str | None
    by: uuid.UUID
    by_email: str | None
    at: datetime


class IssueResponse(BaseModel):
    id: uuid.UUID
    order_line_id: uuid.UUID
    code: str
    severity: str
    message: str
    column_ref: str | None
    acknowledged_by: uuid.UUID | None
    acknowledged_by_email: str | None = None
    acknowledged_at: datetime | None
    carried_forward: bool
    approval_reason_code: str | None = None
    approval_remark: str | None = None
    approval_expires_at: datetime | None = None
    recommendation: IssueRecommendation | None = None
    rejection: IssueRejection | None = None


class ReviewAuditEntryResponse(BaseModel):
    """One audit_log row of the snapshot's issue-review trail. `actor_email`
    is resolved server-side so an ACCOUNTANT can read it without the
    OWNER-only users endpoint."""

    id: uuid.UUID
    action: str
    at: datetime
    actor_email: str | None
    after: dict | None
