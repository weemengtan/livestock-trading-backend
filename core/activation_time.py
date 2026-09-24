"""When a scheduled DNBP model goes live.

Ms. Bing sets the model up from Singapore, so an activation *date* means
00:00 on that date in Singapore. This is deliberately separate from
core/business_time.py: that module decides which Melbourne trade date an
event belongs to; this one turns a chosen calendar date into the exact
instant a configuration switches. The instant is what is stored (timestamptz,
UTC), so nothing downstream depends on any zone.

For Melbourne buyers the switch lands at 02:00 (AEST) or 03:00 (AEDT) on that
date — `describe_activation` returns both so the UI can say so.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

ACTIVATION_TZ = ZoneInfo("Asia/Singapore")
_MELBOURNE = ZoneInfo("Australia/Melbourne")


def activation_instant_for(activation_date: date) -> datetime:
    """00:00 Singapore time on `activation_date`, as a UTC instant."""
    return datetime.combine(activation_date, time.min, tzinfo=ACTIVATION_TZ).astimezone(UTC)


def activation_date_of(instant: datetime) -> date:
    """The Singapore calendar date `instant` falls on (inverse of the above
    for any stored activation instant)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(ACTIVATION_TZ).date()


def singapore_today(now: datetime | None = None) -> date:
    return activation_date_of(now or datetime.now(UTC))


def describe_activation(instant: datetime) -> dict[str, str]:
    """The switch moment in both zones, for display."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return {
        "singapore": instant.astimezone(ACTIVATION_TZ).strftime("%Y-%m-%d %H:%M"),
        "melbourne": instant.astimezone(_MELBOURNE).strftime("%Y-%m-%d %H:%M"),
    }
