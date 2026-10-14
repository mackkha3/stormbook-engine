"""Slot selection, spreading, and formatting. Pure functions, no I/O."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_WINDOW = re.compile(r"^(?:(morning|afternoon|evening)|(after|before) (\d{1,2}):(\d{2}))$")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Past this many days out, a weekday alone could mean two different days.
WEEKDAY_ONLY_DAYS = 6
# Where "closest opening" is measured from when the homeowner named a part of the day.
_ANCHORS = {"morning": time(9, 0), "afternoon": time(14, 0), "evening": time(17, 30)}


def slot_key(dt: datetime) -> str:
    """Canonical string for a slot — what gets stored and compared."""
    return dt.astimezone(UTC).isoformat()


def from_key(key: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(key).astimezone(tz)


def format_slot(dt: datetime, tz: ZoneInfo) -> str:
    """`Tue 10/7 at 10:00 AM`, in the client's timezone."""
    local = dt.astimezone(tz)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return (
        f"{_DAYS[local.weekday()]} {local.month}/{local.day} "
        f"at {hour}:{local.minute:02d} {meridiem}"
    )


def friendly_slot(dt: datetime, tz: ZoneInfo, now: datetime) -> str:
    """How a slot reads in a text to the homeowner: `Wednesday at 8 AM`, `Thursday at noon`.

    The date is added (`Wednesday 10/14 at 8 AM`) only when the slot is more than
    6 days after `now`. `format_slot` stays the dated form for notes, logs and the model.
    """
    local = dt.astimezone(tz)
    day = _WEEKDAYS[local.weekday()]
    if (local.date() - now.astimezone(tz).date()).days > WEEKDAY_ONLY_DAYS:
        day = f"{day} {local.month}/{local.day}"
    if (local.hour, local.minute) == (12, 0):
        return f"{day} at noon"
    hour = local.hour % 12 or 12
    minutes = f":{local.minute:02d}" if local.minute else ""
    return f"{day} at {hour}{minutes} {'AM' if local.hour < 12 else 'PM'}"


def part_of_day(dt: datetime) -> str:
    if dt.hour < 12:
        return "morning"
    return "afternoon" if dt.hour < 17 else "evening"


def eligible(
    free: list[datetime],
    *,
    now: datetime,
    tz: ZoneInfo,
    min_lead_minutes: int,
    window_days: int,
    exclude: set[str] | None = None,
) -> list[datetime]:
    """Free slots inside [now + lead time, now + search window], minus `exclude`."""
    earliest = now + timedelta(minutes=min_lead_minutes)
    latest = now + timedelta(days=window_days)
    exclude = exclude or set()
    kept = {
        slot_key(s): s.astimezone(tz)
        for s in free
        if earliest <= s <= latest and slot_key(s) not in exclude
    }
    return sorted(kept.values())


def _spread_score(candidate: datetime, others: list[datetime]) -> tuple[int, int]:
    """Higher is better: a different day AND part of day from everything in `others`."""
    new_day = all(candidate.date() != o.date() for o in others)
    new_part = all(part_of_day(candidate) != part_of_day(o) for o in others)
    far_apart = all(abs(candidate - o) >= timedelta(hours=3) for o in others)
    return (int(new_day) + int(new_part), int(far_apart))


def pick_spread(
    candidates: list[datetime], n: int, *, avoid: list[datetime] | None = None
) -> list[datetime]:
    """Pick up to `n` slots, soonest-first but spread out.

    Each pick prefers a different day and a different part of the day from the
    picks so far and from `avoid` (slots the homeowner just rejected); ties go
    to the earliest slot, so back-to-back times are only used as a last resort.
    """
    pool = sorted(candidates)
    picked: list[datetime] = []
    while pool and len(picked) < n:
        reference = picked + list(avoid or [])
        best = max(pool, key=lambda s: (_spread_score(s, reference), -s.timestamp()))
        picked.append(best)
        pool.remove(best)
    return sorted(picked)


def valid_window(window: str | None) -> bool:
    if window is None:
        return True
    m = _WINDOW.match(window)
    return bool(m) and (m.group(1) is not None or (int(m.group(3)) < 24 and int(m.group(4)) < 60))


def _in_window(dt: datetime, window: str | None) -> bool:
    if window is None:
        return True
    m = _WINDOW.match(window)
    if not m:
        return False
    if m.group(1):
        return part_of_day(dt) == m.group(1)
    bound = time(int(m.group(3)), int(m.group(4)))
    return dt.time() >= bound if m.group(2) == "after" else dt.time() < bound


def matching(
    candidates: list[datetime], preferred_date: date | None, window: str | None
) -> list[datetime]:
    """Slots on the requested day and/or inside the requested time window."""
    return [
        s
        for s in sorted(candidates)
        if (preferred_date is None or s.date() == preferred_date) and _in_window(s, window)
    ]


def target_time(
    preferred_date: date | None, window: str | None, *, now: datetime, tz: ZoneInfo
) -> datetime:
    """The single moment a request points at, for measuring "closest opening"."""
    m = _WINDOW.match(window) if window else None
    if m and m.group(1):
        at = _ANCHORS[m.group(1)]
    elif m:
        at = time(int(m.group(3)), int(m.group(4)))
    else:
        at = time(12, 0)
    day = preferred_date or now.astimezone(tz).date()
    return datetime.combine(day, at, tzinfo=tz)


def closest(candidates: list[datetime], target: datetime, n: int) -> list[datetime]:
    """The `n` slots nearest `target`, returned in chronological order."""
    return sorted(sorted(candidates, key=lambda s: (abs(s - target), s))[:n])
