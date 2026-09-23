"""POST /buy-instructions (§9.6, §13.1) and the full instruction lifecycle,
Phase 4.

`generate` reads every ACTIVE line's own `bing_dnbp` straight from
`order_workings` — the same source `services.publication_service`'s
`compute_publication_lines` reads — without ever touching the species-keyed
`dnbp_publication_lines` (see dnbp-publication-is-species-keyed): the Buy
Instruction is the other, order-keyed consumer of `AC`. No `DnbpPublication`
needs to exist yet for this: `publication_id` stays `None` through DRAFT.

Status machine (revised — a `DnbpPublication` must never come into existence
except as the result of publishing an already-approved instruction, so
"issue" and "publish" are now the same action):
  DRAFT --approve (OWNER only)--> DRAFT (stamps approved_by/approved_at only)
  DRAFT --publish (requires approved_by set)--> ISSUED (creates the
    DnbpPublication via publication_service.publish and links it)
  ISSUED --buyer acknowledges--> ACKNOWLEDGED
  ACKNOWLEDGED --reconcile-close (OWNER or ACCOUNTANT)--> RECONCILED

`reconcile-close` and the fill routes are not in §9.6's literal route list —
same documented-deviation pattern as Phase 3's `dnbp_publication_deliveries`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.business_time import business_today
from core.errors import (
    FillsNotAllowed,
    InstructionNotApproved,
    InvalidInstructionTransition,
    NotFound,
    NothingToInstruct,
)
from core.trading_calendar import trading_week_bounds
from models.buy_instruction import BuyInstruction, BuyInstructionLine, BuyInstructionLineFill
from models.dnbp_publication import DnbpPublication, DnbpPublicationLine
from models.enums import BuyInstructionStatus
from models.order_snapshot import OrderSnapshot
from repositories import buy_instructions as buy_instructions_repo
from repositories import order_snapshots as order_snapshots_repo
from repositories import order_workings as order_workings_repo
from repositories import users as users_repo
from services import audit_service, publication_service

MONEY_ZERO = Decimal("0")


async def generate(
    db: AsyncSession,
    *,
    snapshot: OrderSnapshot,
    actor_id: uuid.UUID,
    trade_date: date | None,
    note: str | None,
) -> tuple[BuyInstruction, list[BuyInstructionLine]]:
    rows = await order_workings_repo.list_active_with_bing_dnbp(db, snapshot.id)
    if not rows:
        raise NothingToInstruct()

    # §13.1's header shows a single trade date for the whole instruction.
    # §9.6 doesn't list an explicit `trade_date` parameter, so — same kind
    # of documented interpretive choice Phase 3b made for the impact-preview
    # formula — this defaults to the snapshot's own `as_of_date` (the date
    # the abattoir's submission covers) unless the caller overrides it.
    effective_trade_date = trade_date or snapshot.as_of_date or business_today()

    existing_for_date = await buy_instructions_repo.list_for_org(db, snapshot.org_id, trade_date=effective_trade_date)
    version = len(existing_for_date) + 1
    instruction_no = f"BI-{effective_trade_date:%Y%m%d}"

    instruction = BuyInstruction(
        org_id=snapshot.org_id,
        instruction_no=instruction_no,
        version=version,
        trade_date=effective_trade_date,
        snapshot_id=snapshot.id,
        prepared_by=actor_id,
        note=note,
        status=BuyInstructionStatus.DRAFT,
    )
    await buy_instructions_repo.create(db, instruction)

    lines: list[BuyInstructionLine] = []
    for seq, (order_line, workings) in enumerate(
        sorted(rows, key=lambda pair: pair[0].line_no), start=1
    ):
        expected_heads = order_line.estimated_heads or MONEY_ZERO
        weight_requirement_kg = order_line.avg_weight_kg or MONEY_ZERO
        dnbp_per_kg = workings.bing_dnbp  # AC — order-keyed, never dnbp_publication_lines. See module docstring.
        schw_kg = expected_heads * weight_requirement_kg
        expected_livestock_cost = dnbp_per_kg * weight_requirement_kg * expected_heads  # [D6] resolved — AC, not L

        lines.append(
            BuyInstructionLine(
                instruction_id=instruction.id,
                order_line_id=order_line.id,
                seq=seq,
                contract_no=order_line.contract_no,
                species=order_line.species,
                schw_kg=schw_kg,
                expected_heads=expected_heads,
                weight_requirement_kg=weight_requirement_kg,
                dnbp_per_kg=dnbp_per_kg,
                peters_expectation=order_line.expected_livestock_cost_per_kg,
                expected_livestock_cost=expected_livestock_cost,
            )
        )
    await buy_instructions_repo.add_lines(db, lines)

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.generated",
        entity="buy_instruction",
        entity_id=instruction.id,
        after={"snapshot_id": str(snapshot.id), "line_count": len(lines)},
    )

    return instruction, lines


async def update_note(db: AsyncSession, instruction: BuyInstruction, *, note: str | None, actor_id: uuid.UUID) -> None:
    if instruction.status != BuyInstructionStatus.DRAFT:
        raise InvalidInstructionTransition("Only a DRAFT instruction can be edited.")
    before = {"note": instruction.note}
    instruction.note = note
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.updated",
        entity="buy_instruction",
        entity_id=instruction.id,
        before=before,
        after={"note": note},
    )


async def approve(db: AsyncSession, instruction: BuyInstruction, *, actor_id: uuid.UUID) -> BuyInstruction:
    if instruction.status != BuyInstructionStatus.DRAFT:
        raise InvalidInstructionTransition("Only a DRAFT instruction can be approved.")
    instruction.approved_by = actor_id
    instruction.approved_at = datetime.now(UTC)
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.approved",
        entity="buy_instruction",
        entity_id=instruction.id,
        after={"approved_by": str(actor_id)},
    )
    return instruction


async def publish(
    db: AsyncSession, instruction: BuyInstruction, *, actor_id: uuid.UUID
) -> tuple[BuyInstruction, DnbpPublication, list[DnbpPublicationLine]]:
    """The action that used to be the Workbench's standalone "Publish"
    button (POST /publications), moved to here and gated on approval: a
    `DnbpPublication` (what makes DNBP live on the buyer's PWA) must never
    come into existence except as the result of publishing an
    already-approved instruction — otherwise a buyer could act on a price
    whose underlying order-level cost exposure (Peters Expectation vs.
    expected livestock cost) was never reviewed by anyone. Reuses
    `publication_service.publish` untouched: its existing BLOCK-refusal /
    mandatory-WARN-acknowledgment gate keeps working exactly as before.

    Returns the publication and its lines (not just the instruction) so the
    caller can run `delivery_service.fan_out` — the actual buyer
    notification — the same way the old direct route did; this function
    intentionally does not call it itself, since committing before fan-out
    (and again after) is the API layer's job here, same as every other
    service in this codebase."""
    if instruction.status != BuyInstructionStatus.DRAFT:
        raise InvalidInstructionTransition("Only a DRAFT instruction can be published.")
    if instruction.approved_by is None:
        raise InstructionNotApproved()

    snapshot = await order_snapshots_repo.get_by_id(db, instruction.snapshot_id)
    if snapshot is None:
        raise NotFound("Snapshot")

    publication, lines = await publication_service.publish(db, snapshot, actor_id=actor_id, notes=None)

    instruction.publication_id = publication.id
    instruction.status = BuyInstructionStatus.ISSUED
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.published",
        entity="buy_instruction",
        entity_id=instruction.id,
        after={"publication_id": str(publication.id)},
    )
    return instruction, publication, lines


async def acknowledge(db: AsyncSession, instruction: BuyInstruction, *, buyer_id: uuid.UUID) -> BuyInstruction:
    if instruction.status != BuyInstructionStatus.ISSUED:
        raise InvalidInstructionTransition("Only an ISSUED instruction can be acknowledged.")
    instruction.status = BuyInstructionStatus.ACKNOWLEDGED
    await audit_service.write(
        db,
        actor_id=buyer_id,
        action="buy_instruction.acknowledged",
        entity="buy_instruction",
        entity_id=instruction.id,
    )
    return instruction


async def reconcile_close(db: AsyncSession, instruction: BuyInstruction, *, actor_id: uuid.UUID) -> BuyInstruction:
    if instruction.status != BuyInstructionStatus.ACKNOWLEDGED:
        raise InvalidInstructionTransition("Only an ACKNOWLEDGED instruction can be closed as reconciled.")
    instruction.status = BuyInstructionStatus.RECONCILED
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.reconciled",
        entity="buy_instruction",
        entity_id=instruction.id,
    )
    return instruction


async def add_fill(
    db: AsyncSession,
    instruction: BuyInstruction,
    line: BuyInstructionLine,
    *,
    label: str,
    kg_amount: Decimal,
    actor_id: uuid.UUID,
    is_outsourced: bool = False,
    outsourced_buyer_name: str | None = None,
) -> BuyInstructionLineFill:
    if line.instruction_id != instruction.id:
        raise NotFound("Buy instruction line")
    if instruction.status not in (BuyInstructionStatus.ISSUED, BuyInstructionStatus.ACKNOWLEDGED):
        raise FillsNotAllowed()
    fill = BuyInstructionLineFill(
        line_id=line.id,
        label=label,
        kg_amount=kg_amount,
        entered_by=actor_id,
        entered_at=datetime.now(UTC),
        is_outsourced=is_outsourced,
        outsourced_buyer_name=outsourced_buyer_name if is_outsourced else None,
    )
    await buy_instructions_repo.add_fill(db, fill)
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.fill_added",
        entity="buy_instruction_line",
        entity_id=line.id,
        after={
            "label": label,
            "kg_amount": str(kg_amount),
            "is_outsourced": is_outsourced,
            "outsourced_buyer_name": outsourced_buyer_name if is_outsourced else None,
        },
    )
    return fill


async def remove_fill(
    db: AsyncSession, instruction: BuyInstruction, fill: BuyInstructionLineFill, *, actor_id: uuid.UUID
) -> None:
    if instruction.status not in (BuyInstructionStatus.ISSUED, BuyInstructionStatus.ACKNOWLEDGED):
        raise FillsNotAllowed()
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="buy_instruction.fill_removed",
        entity="buy_instruction_line",
        entity_id=fill.line_id,
        before={"label": fill.label, "kg_amount": str(fill.kg_amount)},
    )
    await buy_instructions_repo.delete_fill(db, fill)


def schw_kg_for_entries(entries: list) -> Decimal:
    """Ordered/Bought SCHW's shared per-entry math (§13.1: `heads x weight
    requirement`, applied here to actual buy_entries rather than an
    instruction line's planned figures) — factored out so Phase 5's
    fulfilment panel (services/analytics_service.py, grouped by trade date
    across ALL instructions) reuses this exact formula instead of
    re-deriving it, per phase05-instructions.txt's non-negotiable #6."""
    return sum((e.weight_kg * e.head_count for e in entries), MONEY_ZERO)


@dataclass(slots=True)
class SaleyardReconciliation:
    saleyard: str
    species: str
    schw_kg: Decimal
    heads: int
    actual_cost: Decimal
    outsourced_heads: int
    outsourced_cost: Decimal


@dataclass(slots=True)
class ReconciliationSummary:
    actual_heads: int
    expected_heads: Decimal
    ordered_schw: Decimal  # A
    bought_schw: Decimal  # B
    surplus_shortfall_schw: Decimal  # A - B
    expected_cost: Decimal  # C
    actual_cost: Decimal  # D
    cost_variance: Decimal  # C - D
    outsourced_heads: int
    outsourced_cost: Decimal  # subset of D bought via a 3rd-party buyer — see SaleyardReconciliation docstring


async def compute_reconciliation(
    db: AsyncSession, instruction: BuyInstruction
) -> tuple[list[SaleyardReconciliation], ReconciliationSummary]:
    """§13.1's reconciliation and summary blocks, non-negotiable #6: live-
    computed on every read (never cached), scoped to the Melbourne Mon-Sun
    trading week containing `instruction.trade_date`, grouped by whichever
    saleyard+species pairs actually have `buy_entries` in that window — never
    hard-coded to a fixed set of saleyard columns. Grouped by species as well
    as saleyard (not saleyard alone) so unrelated species logged at the same
    saleyard in the same week don't net against each other into one figure.
    "Actual Cost" is the livestock cost only (price_per_head x head_count),
    the same basis as the received `V` (`total_livestock_cost = L x F`, also
    excluding freight) — freight is tracked separately per entry, not folded
    into this figure.

    `outsourced_heads`/`outsourced_cost` are the subset of that same row's
    heads/actual_cost that came from buy_entries flagged `is_outsourced`
    (§8's Outsourced Buy Log) — never a separate row, since an outsourced
    buy is still counted toward this saleyard+species pair's fulfilment
    exactly like any other entry (models/buy_entry.py's `is_outsourced`
    docstring). Surfaced so a 3rd-party buyer's commission cost — not
    tracked here, but computed downstream from these heads/kg — can be
    apportioned without re-querying buy_entries directly."""
    lines = await buy_instructions_repo.list_lines(db, instruction.id)
    week_start, week_end = trading_week_bounds(instruction.trade_date)
    entries = await buy_instructions_repo.list_org_buy_entries_in_window(
        db, instruction.org_id, week_start=week_start, week_end=week_end
    )

    by_saleyard_species: dict[tuple[str, str], list] = {}
    for entry in entries:
        by_saleyard_species.setdefault((entry.saleyard, entry.species), []).append(entry)

    saleyard_rows = [
        SaleyardReconciliation(
            saleyard=saleyard,
            species=species,
            schw_kg=schw_kg_for_entries(es),
            heads=sum(e.head_count for e in es),
            actual_cost=sum((e.price_per_head * e.head_count for e in es), MONEY_ZERO),
            outsourced_heads=sum(e.head_count for e in es if e.is_outsourced),
            outsourced_cost=sum((e.price_per_head * e.head_count for e in es if e.is_outsourced), MONEY_ZERO),
        )
        for (saleyard, species), es in sorted(by_saleyard_species.items())
    ]

    ordered_schw = sum((line.schw_kg for line in lines), MONEY_ZERO)
    expected_heads = sum((line.expected_heads for line in lines), MONEY_ZERO)
    expected_cost = sum((line.expected_livestock_cost for line in lines), MONEY_ZERO)
    bought_schw = sum((row.schw_kg for row in saleyard_rows), MONEY_ZERO)
    actual_heads = sum(row.heads for row in saleyard_rows)
    actual_cost = sum((row.actual_cost for row in saleyard_rows), MONEY_ZERO)
    outsourced_heads = sum(row.outsourced_heads for row in saleyard_rows)
    outsourced_cost = sum((row.outsourced_cost for row in saleyard_rows), MONEY_ZERO)

    summary = ReconciliationSummary(
        actual_heads=actual_heads,
        expected_heads=expected_heads,
        ordered_schw=ordered_schw,
        bought_schw=bought_schw,
        surplus_shortfall_schw=ordered_schw - bought_schw,
        expected_cost=expected_cost,
        actual_cost=actual_cost,
        cost_variance=expected_cost - actual_cost,
        outsourced_heads=outsourced_heads,
        outsourced_cost=outsourced_cost,
    )
    return saleyard_rows, summary


@dataclass(slots=True)
class ReconciliationEntry:
    id: uuid.UUID
    buyer_email: str
    trade_date: date
    agent: str | None
    pen: str | None
    head_count: int
    price_per_head: Decimal
    weight_kg: Decimal
    implied_price_per_kg: Decimal
    is_breach: bool
    breach_reason: str | None
    is_outsourced: bool
    outsourced_buyer_name: str | None
    client_created_at: datetime


async def list_reconciliation_entries(
    db: AsyncSession, instruction: BuyInstruction, *, saleyard: str, species: str
) -> list[ReconciliationEntry]:
    """Drill-down behind one saleyard+species row of compute_reconciliation
    — the individual buy_entries that sum to that row's SCHW/Heads/Actual
    Cost, for audit. Same window/scope as compute_reconciliation itself
    (org-wide, Melbourne Mon-Sun trading week), narrowed to the one group."""
    week_start, week_end = trading_week_bounds(instruction.trade_date)
    entries = await buy_instructions_repo.list_org_buy_entries_in_window(
        db, instruction.org_id, week_start=week_start, week_end=week_end, saleyard=saleyard, species=species
    )
    buyer_ids = {e.buyer_id for e in entries}
    emails: dict[uuid.UUID, str] = {}
    for buyer_id in buyer_ids:
        user = await users_repo.get_by_id(db, buyer_id)
        emails[buyer_id] = user.email if user is not None else "unknown"
    return [
        ReconciliationEntry(
            id=e.id,
            buyer_email=emails[e.buyer_id],
            trade_date=e.trade_date,
            agent=e.agent,
            pen=e.pen,
            head_count=e.head_count,
            price_per_head=e.price_per_head,
            weight_kg=e.weight_kg,
            implied_price_per_kg=e.implied_price_per_kg,
            is_breach=e.is_breach,
            breach_reason=e.breach_reason,
            is_outsourced=e.is_outsourced,
            outsourced_buyer_name=e.outsourced_buyer_name,
            client_created_at=e.client_created_at,
        )
        for e in entries
    ]


def line_balance(line: BuyInstructionLine, fills: list[BuyInstructionLineFill]) -> Decimal:
    """Balance is always derived, never stored (non-negotiable #4)."""
    return line.schw_kg - sum((fill.kg_amount for fill in fills), MONEY_ZERO)
