"""One place that decides which calendar date a business event belongs to.

Every business date in the app (`trade_date`, "today" for analytics and
default instruction dating) is a calendar date in Australia/Melbourne — the
saleyards, buyers and the 13:00 deadline are all Melbourne — regardless of
where the server runs (a laptop in Singapore, a UTC container) or where the
person looking at it sits. Never use `date.today()`, a UTC date, or the
browser's date for one; `date.today()` is the server's *local* date, so the
same code would give a different business date in every environment.

Timestamps stay instants (`timestamptz`, UTC); only the date a timestamp
falls on is resolved here. IANA zone names, never fixed offsets — Melbourne
observes daylight saving, so its offset from UTC (and from Singapore)
changes twice a year.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Australia/Melbourne")

# A device clock this far ahead of the server is treated as wrong, not as a
# real capture time (see capture_date).
_MAX_CLOCK_SKEW = timedelta(minutes=5)


def business_date_of(instant: datetime) -> date:
    """The Melbourne calendar date `instant` falls on. A naive datetime is
    taken as UTC — the wire format for every timestamp in the API."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(BUSINESS_TZ).date()


def business_today() -> date:
    return business_date_of(datetime.now(UTC))


def capture_date(client_created_at: datetime) -> date:
    """The trade date of an entry a buyer captured at `client_created_at`.

    Derived from the capture instant, never taken from the client, so an
    entry can't be backdated or forward-dated, and an offline entry synced
    hours or days later still lands on the day it was actually made. A
    capture time ahead of the server's clock (a wrong device clock) is
    clamped to now rather than accepted as a future date.
    """
    now = datetime.now(UTC)
    if client_created_at.tzinfo is None:
        client_created_at = client_created_at.replace(tzinfo=UTC)
    if client_created_at > now + _MAX_CLOCK_SKEW:
        client_created_at = now
    return business_date_of(client_created_at)
