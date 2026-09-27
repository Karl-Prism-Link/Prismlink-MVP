from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class BusinessHourLike(Protocol):
    day_of_week: int
    opens_at: object | None
    closes_at: object | None
    is_closed: bool


_OPEN_NOW_RE = re.compile(
    r"\b(?:are\s+you|is\s+(?:the\s+)?salon|is\s+it)\s+open(?:\s+(?:now|today|at\s+the\s+moment))?\b"
    r"|\b(?:open|closed)\s+(?:right\s+)?now\b",
    re.I,
)
_HOURS_RE = re.compile(
    r"\b(?:opening|business)\s+hours\b"
    r"|\bwhat\s+(?:are\s+)?(?:your|the)\s+hours\b"
    r"|\bwhat\s+time\s+do\s+you\s+(?:open|close)\b"
    r"|\bwhen\s+do\s+you\s+(?:open|close)\b",
    re.I,
)

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def is_open_now_question(text: str | None) -> bool:
    return bool(_OPEN_NOW_RE.search(str(text or "")))


def is_business_hours_question(text: str | None) -> bool:
    value = str(text or "")
    return bool(_OPEN_NOW_RE.search(value) or _HOURS_RE.search(value))


def _clock(value: object | None) -> str:
    if value is None:
        return ""
    hour = int(value.hour)
    minute = int(value.minute)
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    if minute:
        return f"{hour12}:{minute:02d} {suffix}"
    return f"{hour12} {suffix}"


def _row_map(hours: Iterable[BusinessHourLike]) -> dict[int, BusinessHourLike]:
    return {int(row.day_of_week): row for row in hours}


def _next_opening(
    rows: dict[int, BusinessHourLike], *, start_day: int
) -> tuple[int, BusinessHourLike] | None:
    for offset in range(1, 8):
        day = (start_day + offset) % 7
        row = rows.get(day)
        if row and not row.is_closed and row.opens_at is not None and row.closes_at is not None:
            return day, row
    return None


def open_now_speech(
    hours: Iterable[BusinessHourLike],
    timezone: str,
    *,
    now: datetime | None = None,
) -> str:
    try:
        zone = ZoneInfo(timezone)
    except Exception:
        zone = ZoneInfo("Pacific/Auckland")

    local_now = now.astimezone(zone) if now is not None else datetime.now(zone)
    rows = _row_map(hours)
    today = local_now.weekday()
    row = rows.get(today)

    if row is None:
        return "I don't have today's opening hours configured. I can take a message for the salon instead."

    if row.is_closed or row.opens_at is None or row.closes_at is None:
        next_open = _next_opening(rows, start_day=today)
        if next_open is None:
            return "We're closed right now. I don't have another opening time configured."
        next_day, next_row = next_open
        return (
            f"No, we're closed now. We're open {_DAY_NAMES[next_day]} from "
            f"{_clock(next_row.opens_at)} to {_clock(next_row.closes_at)}."
        )

    current_time = local_now.timetz().replace(tzinfo=None)
    opens = row.opens_at
    closes = row.closes_at
    if opens <= current_time < closes:
        return f"Yes, we're open now. We close at {_clock(closes)} today."
    if current_time < opens:
        return f"No, we're closed right now. We open at {_clock(opens)} today."

    next_open = _next_opening(rows, start_day=today)
    if next_open is None:
        return f"No, we're closed now. We closed at {_clock(closes)} today."
    next_day, next_row = next_open
    day_phrase = "tomorrow" if next_day == (today + 1) % 7 else _DAY_NAMES[next_day]
    return (
        f"No, we're closed now. We're open {day_phrase} from "
        f"{_clock(next_row.opens_at)} to {_clock(next_row.closes_at)}."
    )


def business_hours_summary_speech(hours: Iterable[BusinessHourLike]) -> str:
    rows = _row_map(hours)
    parts: list[str] = []
    for day in range(7):
        row = rows.get(day)
        if row is None:
            continue
        if row.is_closed or row.opens_at is None or row.closes_at is None:
            parts.append(f"{_DAY_NAMES[day]} closed")
        else:
            parts.append(f"{_DAY_NAMES[day]} {_clock(row.opens_at)} to {_clock(row.closes_at)}")
    if not parts:
        return "I don't have the salon's opening hours configured. I can take a message instead."
    return "Our hours are " + ", ".join(parts) + "."
