"""Which DNBP model is live, and the lifecycle status of every model.

Pure and zero-IO like the rest of domain/engine/. Nothing here is stored: the
live model is a function of the approved schedule and the clock, so it needs
no scheduler and cannot be left stale by a missed job.

A model is:
  CANCELLED         — cancelled before it went live (terminal).
  DRAFT             — saved but not yet approved.
  SCHEDULED         — approved, activation instant still in the future.
  LIVE              — approved, activation instant passed, and no later
                      approved model has passed its own instant.
  RETIRED           — approved and once live, superseded by a later model.
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime


class ModelStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    RETIRED = "RETIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ScheduledModel:
    """The scheduling facts of one model — no prices."""

    id: uuid.UUID
    activation_at: datetime
    approved: bool
    cancelled: bool


def _eligible(model: ScheduledModel) -> bool:
    return model.approved and not model.cancelled


def live_model_id(models: list[ScheduledModel], now: datetime) -> uuid.UUID | None:
    """The approved, non-cancelled model with the latest activation instant
    that is not after `now`. None only when nothing has gone live yet."""
    due = [m for m in models if _eligible(m) and m.activation_at <= now]
    if not due:
        return None
    return max(due, key=lambda m: m.activation_at).id


def status_of(model: ScheduledModel, *, live_id: uuid.UUID | None, now: datetime) -> ModelStatus:
    if model.cancelled:
        return ModelStatus.CANCELLED
    if not model.approved:
        return ModelStatus.DRAFT
    if model.activation_at > now:
        return ModelStatus.SCHEDULED
    return ModelStatus.LIVE if model.id == live_id else ModelStatus.RETIRED


def can_still_change(model: ScheduledModel, *, now: datetime) -> bool:
    """Reschedule / cancel are allowed only until the model has gone live: a
    draft at any time, an approved model only while its instant is still in
    the future. Once it has been live, even briefly, prices were computed
    under it and it is history."""
    if model.cancelled:
        return False
    return (not model.approved) or model.activation_at > now
