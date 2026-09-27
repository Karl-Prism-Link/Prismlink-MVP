from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from apps.api.schemas import AppointmentCreate, AppointmentMove
from database.models import SalonMessage, Tenant
from database.repositories import (
    AppointmentRepository,
    BusinessHoursRepository,
    MessageRepository,
    ServiceRepository,
    StaffRepository,
)
from database.session import DBSession
from database.utils import utcnow
from services.booking import BookingService
from services.calendar.google import GoogleCalendarProviderError
from services.service_names import canonical_service_name, service_match_terms
from services.voice.contact import (
    extract_phone_from_speech,
    is_explicit_affirmation,
    is_management_phone_recovery_statement,
    is_management_phone_unavailable,
    phone_from_recent_transcripts,
    speak_phone_digits,
)
from services.voice.session import LiveCallSession


class VoiceToolset:
    """Validated, tenant-scoped tools exposed to the live voice LLM."""

    def __init__(self, db: DBSession, tenant: Tenant, call_session: LiveCallSession) -> None:
        self.db = db
        self.tenant = tenant
        self.call_session = call_session
        self.booking = BookingService(db, tenant.id)
        self.services = ServiceRepository(db, tenant.id)
        self.staff = StaffRepository(db, tenant.id)
        self.hours = BusinessHoursRepository(db, tenant.id)
        self.appointments = AppointmentRepository(db, tenant.id)
        try:
            self.tz = ZoneInfo(tenant.timezone)
        except ZoneInfoNotFoundError:
            self.tz = ZoneInfo("Pacific/Auckland")

    @staticmethod
    def _normalise(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    _TIME_WORDS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }

    @classmethod
    def _has_date_component(cls, value: str) -> bool:
        phrase = value.strip().lower()
        if not phrase:
            return False
        return bool(
            re.search(
                r"\b(today|tomorrow|next|monday|mon|tuesday|tue|tues|wednesday|wed|"
                r"thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun|"
                r"january|jan|february|feb|march|mar|april|apr|may|june|jun|"
                r"july|jul|august|aug|september|sep|sept|october|oct|november|nov|"
                r"december|dec)\b",
                phrase,
            )
        )

    @classmethod
    def _has_time_component(cls, value: str) -> bool:
        phrase = value.strip().lower()
        if not phrase:
            return False
        word_hours = "|".join(cls._TIME_WORDS)
        return bool(
            re.search(r"\b(1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b", phrase)
            or re.search(
                rf"\b(?:{word_hours})(?:\s+o['’]?clock)?\s*(?:a\.?m\.?|p\.?m\.?)\b", phrase
            )
            or re.search(r"\b(1[0-2]|0?[1-9])\s+o['’]?\s*clock\b", phrase)
            or re.search(rf"\b(?:{word_hours})\s+o['’]?\s*clock\b", phrase)
            or re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", phrase)
            or re.search(r"\b(noon|midday|midnight)\b", phrase)
        )

    @classmethod
    def _looks_like_datetime_phrase(cls, value: str) -> bool:
        return cls._has_date_component(value) or cls._has_time_component(value)

    def _effective_caller_time_phrase(self, caller_time_phrase: str | None) -> str | None:
        """Recover and compose the caller's date/time words across short voice turns.

        Callers naturally split a request across turns ("Monday" ... "two PM").
        Small LLMs also sometimes pass only the most recent fragment to a tool. Build a
        bounded deterministic phrase from the latest date component and latest time
        component instead of trusting a guessed LLM ISO datetime.
        """
        explicit = (caller_time_phrase or "").strip()
        recent = [
            text
            for text in self.call_session.recent_caller_transcripts[-4:]
            if self._looks_like_datetime_phrase(text)
        ]

        primary = explicit if explicit and self._looks_like_datetime_phrase(explicit) else None
        if primary is None and recent:
            primary = recent[-1]
        if primary is None:
            return caller_time_phrase

        if self._has_date_component(primary) and self._has_time_component(primary):
            return primary

        candidates = list(recent)
        if explicit and explicit not in candidates and self._looks_like_datetime_phrase(explicit):
            candidates.append(explicit)

        date_phrase = primary if self._has_date_component(primary) else None
        time_phrase = primary if self._has_time_component(primary) else None
        for text in reversed(candidates):
            if date_phrase is None and self._has_date_component(text):
                date_phrase = text
            if time_phrase is None and self._has_time_component(text):
                time_phrase = text
            if date_phrase and time_phrase:
                break

        if date_phrase and time_phrase:
            if date_phrase == time_phrase:
                return date_phrase
            return f"{date_phrase} {time_phrase}"
        return primary

    @staticmethod
    def _error_payload(exc: HTTPException) -> dict:
        detail = exc.detail
        if isinstance(detail, dict):
            return {"ok": False, **detail}
        return {"ok": False, "code": f"HTTP_{exc.status_code}", "message": str(detail)}

    async def _resolve_service(self, service_name: str):
        target = self._normalise(service_name)
        rows = [row for row in await self.services.list() if row.is_active]

        exact = [row for row in rows if target and target in service_match_terms(row.name)]
        if len(exact) == 1:
            return exact[0], None

        partial = [
            row
            for row in rows
            if target
            and any(target in term or term in target for term in service_match_terms(row.name))
        ]
        if len(partial) == 1:
            return partial[0], None

        # Conservative ASR correction across configured canonical/alias terms.
        # A vague request such as "a cut" remains ambiguous because both cut
        # services score similarly and therefore fail the margin requirement.
        if len(target) >= 5 and rows:
            ranked = sorted(
                (
                    (
                        max(
                            SequenceMatcher(None, target, term).ratio()
                            for term in service_match_terms(row.name)
                        ),
                        row,
                    )
                    for row in rows
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, best_row = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= 0.88 and best_score - second_score >= 0.08:
                return best_row, None

        choices = [canonical_service_name(row.name) for row in (partial or rows)]
        return None, list(dict.fromkeys(choices))[:8]

    async def _resolve_staff(self, staff_name: str | None):
        if not staff_name or self._normalise(staff_name) in {"any", "any staff", "no preference"}:
            return None, None
        target = self._normalise(staff_name)
        rows = [row for row in await self.staff.list() if row.is_active]
        exact = [row for row in rows if self._normalise(row.name) == target]
        if len(exact) == 1:
            return exact[0], None
        partial = [row for row in rows if target in self._normalise(row.name)]
        if len(partial) == 1:
            return partial[0], None
        return None, [row.name for row in (partial or rows)][:8]

    def _parse_local_datetime(self, starts_at_local: str) -> datetime:
        try:
            value = starts_at_local.strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                "Use an ISO date/time such as 2026-08-13T14:00 or 2026-08-13T14:00+12:00."
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self.tz)
        return parsed.astimezone(UTC)

    def _local_iso(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.tz).isoformat(timespec="minutes")

    def _display_local_datetime(self, value: datetime) -> dict:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        local = value.astimezone(self.tz)
        hour12 = local.strftime("%I").lstrip("0") or "12"
        return {
            "weekday": local.strftime("%A"),
            "local_date": local.date().isoformat(),
            "local_time": local.strftime("%H:%M"),
            "spoken_start": f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y')} at {hour12}:{local.strftime('%M %p')}",
        }

    def _resolve_caller_datetime(
        self, caller_time_phrase: str | None
    ) -> tuple[datetime | None, dict | None]:
        """Resolve common caller date/time phrases deterministically in the salon timezone.

        The LLM is not trusted to perform calendar arithmetic. An unambiguous phrase
        such as ``next Monday at 2 pm`` is converted here. Contradictory phrases are
        rejected rather than guessed.
        """
        if not caller_time_phrase:
            return None, None
        phrase = caller_time_phrase.strip().lower()
        now_local = datetime.now(self.tz)

        weekdays = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "tues": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "thur": 3,
            "thurs": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sep": 9,
            "sept": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }

        hour = minute = None
        ambiguous_hour_12 = None
        ambiguous_minute = 0
        meridiem = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b", phrase)
        if meridiem:
            hour = int(meridiem.group(1)) % 12
            if meridiem.group(3).startswith("p"):
                hour += 12
            minute = int(meridiem.group(2) or 0)
        else:
            word_hours = "|".join(self._TIME_WORDS)
            word_meridiem = re.search(
                rf"\b({word_hours})(?:\s+o['’]?clock)?\s*(a\.?m\.?|p\.?m\.?)\b", phrase
            )
            if word_meridiem:
                hour = self._TIME_WORDS[word_meridiem.group(1)] % 12
                if word_meridiem.group(2).startswith("p"):
                    hour += 12
                minute = 0
            else:
                bare_clock = re.search(r"\b(1[0-2]|0?[1-9])\s+o['’]?\s*clock\b", phrase)
                word_bare_clock = re.search(rf"\b({word_hours})\s+o['’]?\s*clock\b", phrase)
                if bare_clock:
                    ambiguous_hour_12 = int(bare_clock.group(1))
                elif word_bare_clock:
                    ambiguous_hour_12 = self._TIME_WORDS[word_bare_clock.group(1)]
                else:
                    clock_numeric = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", phrase)
                    if clock_numeric:
                        numeric_hour = int(clock_numeric.group(1))
                        numeric_minute = int(clock_numeric.group(2))
                        # Streaming STT commonly normalizes spoken phrases such as
                        # "two o'clock" or simply "two" into "02:00" even when the
                        # caller did not say AM/PM. In a voice transcript, 01:00-12:59
                        # without an explicit meridiem therefore remains a 12-hour
                        # ambiguous time. Let configured business hours resolve it
                        # deterministically when exactly one of AM/PM is valid.
                        # 00:xx and 13:xx-23:xx are unambiguous 24-hour times.
                        if 1 <= numeric_hour <= 12:
                            ambiguous_hour_12 = numeric_hour
                            ambiguous_minute = numeric_minute
                        else:
                            hour, minute = numeric_hour, numeric_minute
                    elif re.search(r"\b(noon|midday)\b", phrase):
                        hour, minute = 12, 0
                    elif re.search(r"\bmidnight\b", phrase):
                        hour, minute = 0, 0

        has_date_language = bool(
            re.search(r"\b(today|tomorrow|next)\b", phrase)
            or any(re.search(rf"\b{re.escape(token)}\b", phrase) for token in weekdays)
            or any(re.search(rf"\b{re.escape(token)}\b", phrase) for token in months)
        )
        if has_date_language and hour is None and ambiguous_hour_12 is None:
            return None, {
                "ok": False,
                "code": "NEED_EXACT_TIME",
                "message": "The caller gave a date/day but not an exact appointment time. Ask for a specific time.",
            }

        target_date = None
        month_pattern = "|".join(sorted(months, key=len, reverse=True))
        month_first = re.search(
            rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:[,]?\s+(\d{{4}}))?\b", phrase
        )
        day_first = re.search(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_pattern})(?:[,]?\s+(\d{{4}}))?\b",
            phrase,
        )
        date_match = month_first or day_first
        if date_match:
            if month_first:
                month_token, day_token, year_token = (
                    date_match.group(1),
                    date_match.group(2),
                    date_match.group(3),
                )
            else:
                day_token, month_token, year_token = (
                    date_match.group(1),
                    date_match.group(2),
                    date_match.group(3),
                )
            year = int(year_token) if year_token else now_local.year
            try:
                candidate = datetime(
                    year, months[month_token], int(day_token), tzinfo=self.tz
                ).date()
            except ValueError:
                return None, {
                    "ok": False,
                    "code": "INVALID_DATE",
                    "message": "The caller's stated calendar date is invalid. Ask them to clarify the date.",
                }
            if not year_token and candidate < now_local.date():
                candidate = candidate.replace(year=year + 1)
            target_date = candidate
        elif re.search(r"\btomorrow\b", phrase):
            target_date = now_local.date() + timedelta(days=1)
        elif re.search(r"\btoday\b", phrase):
            target_date = now_local.date()

        weekday_match = None
        for token, index in weekdays.items():
            if re.search(rf"\b{re.escape(token)}\b", phrase):
                weekday_match = (token, index)
                break

        if weekday_match and target_date is not None:
            token, expected = weekday_match
            if target_date.weekday() != expected:
                actual_name = target_date.strftime("%A")
                return None, {
                    "ok": False,
                    "code": "DATE_WEEKDAY_MISMATCH",
                    "message": (
                        f"The caller combined {token.title()} with {target_date.isoformat()}, "
                        f"but that date is {actual_name}. Ask which one they mean; do not suggest a guessed date."
                    ),
                    "weekday": actual_name,
                    "local_date": target_date.isoformat(),
                }

        if target_date is None and weekday_match:
            token, target_weekday = weekday_match
            days_ahead = (target_weekday - now_local.weekday()) % 7
            if re.search(rf"\bnext\s+{re.escape(token)}\b", phrase) and days_ahead == 0:
                days_ahead = 7
            candidate = now_local.date() + timedelta(days=days_ahead)
            if days_ahead == 0 and hour is not None:
                same_day = datetime(
                    candidate.year,
                    candidate.month,
                    candidate.day,
                    hour,
                    minute or 0,
                    tzinfo=self.tz,
                )
                if same_day <= now_local:
                    candidate += timedelta(days=7)
            target_date = candidate

        if target_date is None:
            return None, None
        if ambiguous_hour_12 is not None:
            return None, {
                "ok": False,
                "code": "AMBIGUOUS_MERIDIEM",
                "message": (
                    "The caller said an o'clock time without AM or PM. Infer AM/PM only if exactly one "
                    "interpretation fits the salon's configured business hours; otherwise ask the caller."
                ),
                "target_date": target_date.isoformat(),
                "hour_12": ambiguous_hour_12,
                "minute": ambiguous_minute,
            }
        if hour is None:
            return None, {
                "ok": False,
                "code": "NEED_EXACT_TIME",
                "message": "Ask the caller for an exact appointment time.",
            }

        local = datetime(
            target_date.year, target_date.month, target_date.day, hour, minute or 0, tzinfo=self.tz
        )
        if local <= now_local:
            return None, {
                "ok": False,
                "code": "TIME_IN_PAST",
                "message": "The caller's requested date/time resolves to the past.",
                **self._display_local_datetime(local.astimezone(UTC)),
            }
        return local.astimezone(UTC), None

    def _appointment_start(
        self, starts_at_local: str, caller_time_phrase: str | None
    ) -> tuple[datetime | None, dict | None, str]:
        resolved, resolve_error = self._resolve_caller_datetime(caller_time_phrase)
        if resolve_error:
            return None, resolve_error, "caller_phrase"
        if resolved is not None:
            return resolved, None, "caller_phrase"
        try:
            parsed = self._parse_local_datetime(starts_at_local)
        except ValueError as exc:
            return None, {"ok": False, "code": "INVALID_TIME", "message": str(exc)}, "llm_iso"
        phrase_error = self._validate_caller_time_phrase(parsed, caller_time_phrase)
        if phrase_error:
            return None, phrase_error, "llm_iso"
        return parsed, None, "llm_iso"

    async def _appointment_start_with_business_hours(
        self, starts_at_local: str, caller_time_phrase: str | None, duration_minutes: int
    ) -> tuple[datetime | None, dict | None, str]:
        """Resolve appointment time, using business hours to disambiguate bare o'clock times.

        Example: if the salon is open 09:00-17:00 and the caller says "Monday at 2 o'clock",
        02:00 is invalid while 14:00 fits, so 2 PM is selected deterministically. If both or neither
        interpretation fits, the caller must clarify AM/PM.
        """
        starts_at, error, source = self._appointment_start(starts_at_local, caller_time_phrase)
        if not error or error.get("code") != "AMBIGUOUS_MERIDIEM":
            return starts_at, error, source

        try:
            target_date = datetime.fromisoformat(str(error["target_date"])).date()
            hour_12 = int(error["hour_12"])
            minute = int(error.get("minute", 0))
        except (KeyError, TypeError, ValueError):
            return (
                None,
                {
                    "ok": False,
                    "code": "NEED_MERIDIEM",
                    "message": "Ask the caller whether they mean AM or PM.",
                },
                "caller_phrase",
            )

        am_hour = hour_12 % 12
        pm_hour = am_hour + 12
        candidates: list[datetime] = []
        for candidate_hour in (am_hour, pm_hour):
            local = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                candidate_hour,
                minute,
                tzinfo=self.tz,
            )
            starts = local.astimezone(UTC)
            ends = starts + timedelta(minutes=duration_minutes)
            if starts > datetime.now(UTC) and await self._inside_configured_hours(starts, ends):
                candidates.append(starts)

        if len(candidates) == 1:
            return candidates[0], None, "caller_phrase_business_hours_inferred"
        if len(candidates) == 0:
            return (
                None,
                {
                    "ok": False,
                    "code": "AMBIGUOUS_TIME_OUTSIDE_HOURS",
                    "message": (
                        f"Neither {hour_12} AM nor {hour_12} PM fits the configured business hours on "
                        f"{target_date.strftime('%A')}. Ask the caller for another time."
                    ),
                    "local_date": target_date.isoformat(),
                },
                "caller_phrase",
            )
        return (
            None,
            {
                "ok": False,
                "code": "NEED_MERIDIEM",
                "message": "Both AM and PM could fit the configured business hours. Ask the caller which one they mean.",
                "local_date": target_date.isoformat(),
            },
            "caller_phrase",
        )

    def _validate_caller_time_phrase(
        self, starts_at: datetime, caller_time_phrase: str | None
    ) -> dict | None:
        """Guard LLM date/time conversion against weekday and AM/PM mistakes.

        The LLM supplies the caller's original date/time words verbatim. The tool then
        checks simple, high-value facts (weekday, today/tomorrow, and explicit am/pm)
        against the ISO datetime before any availability or appointment write occurs.
        """
        if not caller_time_phrase:
            return None
        phrase = caller_time_phrase.strip().lower()
        local = starts_at.astimezone(self.tz)
        display = self._display_local_datetime(starts_at)

        weekdays = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "tues": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "thur": 3,
            "thurs": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        for token, expected in weekdays.items():
            if re.search(rf"\b{re.escape(token)}\b", phrase) and local.weekday() != expected:
                return {
                    "ok": False,
                    "code": "DATE_WEEKDAY_MISMATCH",
                    "message": (
                        f"The interpreted date is {display['weekday']} {display['local_date']}, "
                        f"but the caller said {token.title()}. Ask the caller to clarify the date before continuing."
                    ),
                    **display,
                }

        now_local = datetime.now(self.tz)
        if re.search(r"\btomorrow\b", phrase):
            expected_date = now_local.date() + timedelta(days=1)
            if local.date() != expected_date:
                return {
                    "ok": False,
                    "code": "RELATIVE_DATE_MISMATCH",
                    "message": f"The caller said tomorrow, which is {expected_date.isoformat()} in the salon timezone.",
                    **display,
                }
        elif re.search(r"\btoday\b", phrase) and local.date() != now_local.date():
            return {
                "ok": False,
                "code": "RELATIVE_DATE_MISMATCH",
                "message": f"The caller said today, which is {now_local.date().isoformat()} in the salon timezone.",
                **display,
            }

        meridiem = re.search(
            r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b",
            phrase,
        )
        if meridiem:
            expected_hour = int(meridiem.group(1)) % 12
            if meridiem.group(3).startswith("p"):
                expected_hour += 12
            expected_minute = int(meridiem.group(2) or 0)
            if (local.hour, local.minute) != (expected_hour, expected_minute):
                return {
                    "ok": False,
                    "code": "TIME_MERIDIEM_MISMATCH",
                    "message": (
                        f"The caller's stated time ({meridiem.group(0)}) does not match the interpreted "
                        f"time {display['local_time']}. Ask the caller to confirm the time."
                    ),
                    **display,
                }
        return None

    async def _business_hours_status(self, starts_at: datetime, ends_at: datetime) -> dict:
        """Return an explicit business-hours verdict for an appointment window.

        Availability and opening-hours are separate concepts. Returning the reason
        prevents the LLM from describing a calendar conflict as the salon being closed.
        """
        configured = await self.hours.list()
        local_start = starts_at.astimezone(self.tz)
        local_end = ends_at.astimezone(self.tz)
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day = next((row for row in configured if row.day_of_week == local_start.weekday()), None)

        # No configured hours means the MVP does not impose an hours restriction.
        if not configured or day is None:
            return {
                "within_business_hours": True,
                "day": day_names[local_start.weekday()],
                "opens_at": None,
                "closes_at": None,
                "hours_configured": False,
            }

        if day.is_closed or day.opens_at is None or day.closes_at is None:
            return {
                "within_business_hours": False,
                "day": day_names[local_start.weekday()],
                "opens_at": None,
                "closes_at": None,
                "hours_configured": True,
            }

        within = (
            local_start.date() == local_end.date()
            and day.opens_at <= local_start.time().replace(tzinfo=None)
            and local_end.time().replace(tzinfo=None) <= day.closes_at
        )
        return {
            "within_business_hours": within,
            "day": day_names[local_start.weekday()],
            "opens_at": day.opens_at.strftime("%H:%M"),
            "closes_at": day.closes_at.strftime("%H:%M"),
            "hours_configured": True,
        }

    async def _inside_configured_hours(self, starts_at: datetime, ends_at: datetime) -> bool:
        status = await self._business_hours_status(starts_at, ends_at)
        return bool(status["within_business_hours"])

    def _idempotency_key(self, action: str, *values: object) -> str:
        seed = "|".join([str(self.call_session.call_id or "unstarted"), action, *map(str, values)])
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return f"voice-{action}-{digest}"

    def _appointment_reference_matches(
        self, starts_at: datetime, caller_time_phrase: str | None
    ) -> bool:
        """Match simple caller day/date references against an existing appointment.

        This is deliberately conservative: only references we can validate deterministically
        (weekday/today/tomorrow) are used as filters. Unknown wording does not exclude a match.
        """
        if not caller_time_phrase:
            return True
        phrase = caller_time_phrase.casefold()
        local = starts_at.astimezone(self.tz)
        weekdays = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "tues": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "thur": 3,
            "thurs": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        mentioned = [
            expected
            for token, expected in weekdays.items()
            if re.search(rf"\b{re.escape(token)}\b", phrase)
        ]
        if mentioned and local.weekday() not in mentioned:
            return False
        now_local = datetime.now(self.tz)
        if re.search(r"\btoday\b", phrase) and local.date() != now_local.date():
            return False
        if re.search(r"\btomorrow\b", phrase) and local.date() != now_local.date() + timedelta(
            days=1
        ):
            return False
        meridiem = re.search(
            r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b",
            phrase,
        )
        if meridiem:
            expected_hour = int(meridiem.group(1)) % 12
            if meridiem.group(3).startswith("p"):
                expected_hour += 12
            if (local.hour, local.minute) != (expected_hour, int(meridiem.group(2) or 0)):
                return False
        return True

    async def _appointment_details(self, row) -> dict:
        service = await self.services.get(row.service_id)
        staff = await self.staff.get(row.staff_member_id) if row.staff_member_id else None
        display = self._display_local_datetime(row.starts_at)
        return {
            "appointment_id": str(row.id),
            "customer_name": row.customer_name,
            "starts_at": self._local_iso(row.starts_at),
            "ends_at": self._local_iso(row.ends_at),
            "status": row.status,
            "service_id": str(row.service_id),
            "service": canonical_service_name(service.name) if service else None,
            "duration_minutes": service.duration_minutes
            if service
            else int((row.ends_at - row.starts_at).total_seconds() // 60),
            "staff": staff.name if staff else None,
            **display,
        }

    async def check_reschedule_availability(
        self,
        appointment_id: str,
        starts_at_local: str,
        caller_time_phrase: str | None = None,
    ) -> dict:
        """Preflight a reschedule using the existing appointment's service/staff/duration.

        Generic booking availability is unsafe here because the LLM may pass a service ID
        where a label is expected, or may accidentally change the service/duration.
        """
        verified_phone = self._verified_management_phone_from_caller()
        if not verified_phone:
            return self._management_identity_error()
        try:
            appointment_uuid = UUID(appointment_id)
        except ValueError:
            return {
                "ok": False,
                "code": "INVALID_APPOINTMENT_ID",
                "message": "Appointment ID is invalid.",
            }
        existing = await self.appointments.get(appointment_uuid)
        if existing is None:
            return {
                "ok": False,
                "code": "APPOINTMENT_NOT_FOUND",
                "message": "Appointment not found.",
            }
        if existing.customer_phone != verified_phone:
            return {
                "ok": False,
                "code": "APPOINTMENT_OWNERSHIP_MISMATCH",
                "message": "That appointment is not associated with the verified booking phone number.",
                "identity_verified": True,
                "instruction": "Do not reveal appointment details. Ask the caller to verify the booking phone number.",
            }
        service = await self.services.get(existing.service_id)
        if service is None:
            return {
                "ok": False,
                "code": "SERVICE_NOT_FOUND",
                "message": "Appointment service is unavailable.",
            }

        effective_time_phrase = self._effective_caller_time_phrase(caller_time_phrase)
        (
            starts_at,
            datetime_error,
            datetime_source,
        ) = await self._appointment_start_with_business_hours(
            starts_at_local, effective_time_phrase, service.duration_minutes
        )
        if datetime_error:
            return datetime_error
        assert starts_at is not None
        ends_at = starts_at + timedelta(minutes=service.duration_minutes)
        hours_status = await self._business_hours_status(starts_at, ends_at)
        if not hours_status["within_business_hours"]:
            return {
                "ok": True,
                "available": False,
                "availability_reason": "outside_business_hours",
                "business_hours_for_day": hours_status,
                "service": canonical_service_name(service.name),
                "duration_minutes": service.duration_minutes,
                "current_spoken_start": self._display_local_datetime(existing.starts_at)[
                    "spoken_start"
                ],
                "requested_spoken_start": self._display_local_datetime(starts_at)["spoken_start"],
                "starts_at": self._local_iso(starts_at),
                "ends_at": self._local_iso(ends_at),
                "caller_time_phrase": effective_time_phrase,
                "datetime_source": datetime_source,
            }
        try:
            calendar_available = await self.booking.calendar.is_available(
                tenant_id=self.tenant.id,
                starts_at=starts_at,
                ends_at=ends_at,
                staff_member_id=existing.staff_member_id,
                exclude_appointment_id=existing.id,
            )
        except GoogleCalendarProviderError as exc:
            return {"ok": False, "code": "CALENDAR_UNAVAILABLE", "message": str(exc)}

        old_display = self._display_local_datetime(existing.starts_at)
        new_display = self._display_local_datetime(starts_at)
        existing_staff = (
            await self.staff.get(existing.staff_member_id) if existing.staff_member_id else None
        )
        available = bool(calendar_available)
        result = {
            "ok": True,
            "available": available,
            "availability_reason": "available" if available else "calendar_conflict",
            "appointment_id": str(existing.id),
            "customer_name": existing.customer_name,
            "service": canonical_service_name(service.name),
            "duration_minutes": service.duration_minutes,
            "staff": existing_staff.name if existing_staff else None,
            "current_starts_at": self._local_iso(existing.starts_at),
            "current_spoken_start": old_display["spoken_start"],
            "starts_at": self._local_iso(starts_at),
            "ends_at": self._local_iso(ends_at),
            "requested_spoken_start": new_display["spoken_start"],
            "spoken_start": new_display["spoken_start"],
            "caller_time_phrase": effective_time_phrase,
            "datetime_source": datetime_source,
            "business_hours_for_day": hours_status,
        }
        if available:
            fingerprint = self._idempotency_key("prepare-reschedule", existing.id, starts_at)
            self.call_session.prepare_reschedule(fingerprint)
            result.update(
                {
                    "ready_to_confirm": True,
                    "instruction": (
                        "Read back current_spoken_start and requested_spoken_start exactly and ask one direct confirmation. "
                        "Do not call it booked. After a new explicit yes, call reschedule_appointment with confirmed=true."
                    ),
                }
            )
        else:
            result["instruction"] = (
                "Say the requested time is unavailable; do not claim the salon is closed."
            )
        return result

    async def get_salon_information(self, question: str) -> dict:
        services = [row for row in await self.services.list() if row.is_active]
        staff = [row for row in await self.staff.list() if row.is_active]
        hours = await self.hours.list()
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        formatted_hours = []
        for row in hours:
            if row.is_closed:
                formatted_hours.append(f"{day_names[row.day_of_week]}: closed")
            elif row.opens_at and row.closes_at:
                formatted_hours.append(
                    f"{day_names[row.day_of_week]}: {row.opens_at.strftime('%H:%M')}-{row.closes_at.strftime('%H:%M')}"
                )
        self.call_session.set_resolution(
            "enquiry_resolved",
            f"Answered a salon enquiry about: {question[:180]}",
        )
        return {
            "ok": True,
            "salon_name": self.tenant.name,
            "phone_number": self.tenant.phone_number,
            "timezone": self.tenant.timezone,
            "services": [
                {
                    "name": canonical_service_name(row.name),
                    "duration_minutes": row.duration_minutes,
                    "description": row.description,
                }
                for row in services
            ],
            "staff": [row.name for row in staff],
            "business_hours": formatted_hours,
            "instruction": "Answer only from these configured details. If the requested fact is absent, take a message rather than inventing it.",
        }

    async def check_availability(
        self,
        service_name: str,
        starts_at_local: str,
        staff_name: str | None = None,
        caller_time_phrase: str | None = None,
    ) -> dict:
        service, service_choices = await self._resolve_service(service_name)
        if service is None:
            return {
                "ok": False,
                "code": "SERVICE_NOT_RESOLVED",
                "message": "The service name is missing or ambiguous.",
                "choices": service_choices,
            }
        staff, staff_choices = await self._resolve_staff(staff_name)
        if staff_choices is not None:
            return {
                "ok": False,
                "code": "STAFF_NOT_RESOLVED",
                "message": "The staff preference is ambiguous.",
                "choices": staff_choices,
            }
        effective_time_phrase = self._effective_caller_time_phrase(caller_time_phrase)
        (
            starts_at,
            datetime_error,
            datetime_source,
        ) = await self._appointment_start_with_business_hours(
            starts_at_local, effective_time_phrase, service.duration_minutes
        )
        if datetime_error:
            return datetime_error
        assert starts_at is not None
        if starts_at <= datetime.now(UTC):
            return {
                "ok": False,
                "code": "TIME_IN_PAST",
                "message": "The requested time is in the past.",
            }
        try:
            calendar_available, ends_at = await self.booking.availability(
                starts_at=starts_at,
                service_id=service.id,
                staff_member_id=staff.id if staff else None,
            )
        except HTTPException as exc:
            return self._error_payload(exc)

        hours_status = await self._business_hours_status(starts_at, ends_at)
        within_business_hours = bool(hours_status["within_business_hours"])
        available = bool(calendar_available and within_business_hours)
        if not within_business_hours:
            availability_reason = "outside_business_hours"
        elif not calendar_available:
            availability_reason = "calendar_conflict"
        else:
            availability_reason = "available"

        alternatives: list[str] = []
        if not available:
            for offset in (30, 60, -30, 90):
                candidate = starts_at + timedelta(minutes=offset)
                if candidate <= datetime.now(UTC):
                    continue
                try:
                    candidate_available, candidate_end = await self.booking.availability(
                        starts_at=candidate,
                        service_id=service.id,
                        staff_member_id=staff.id if staff else None,
                    )
                except HTTPException:
                    continue
                if candidate_available and await self._inside_configured_hours(
                    candidate, candidate_end
                ):
                    alternatives.append(self._local_iso(candidate))
                if len(alternatives) == 3:
                    break
        return {
            "ok": True,
            "available": bool(available),
            "availability_reason": availability_reason,
            "calendar_available": bool(calendar_available),
            "within_business_hours": within_business_hours,
            "business_hours_for_day": {
                "day": hours_status["day"],
                "opens_at": hours_status["opens_at"],
                "closes_at": hours_status["closes_at"],
                "hours_configured": hours_status["hours_configured"],
            },
            "service": canonical_service_name(service.name),
            "duration_minutes": service.duration_minutes,
            "staff": staff.name if staff else None,
            "starts_at": self._local_iso(starts_at),
            "ends_at": self._local_iso(ends_at),
            "datetime_source": datetime_source,
            "meridiem_inferred_from_business_hours": datetime_source
            == "caller_phrase_business_hours_inferred",
            "caller_time_phrase": effective_time_phrase,
            **self._display_local_datetime(starts_at),
            "alternatives": alternatives,
            "instruction": (
                "Treat availability_reason and spoken_start as authoritative. The backend resolves common relative date/time "
                "phrases deterministically. If calendar_conflict, say the time is unavailable or busy; do not say the salon "
                "is closed. If outside_business_hours, explain the configured opening hours."
            ),
        }

    async def book_appointment(
        self,
        service_name: str,
        starts_at_local: str,
        customer_name: str,
        customer_phone: str,
        confirmed: bool,
        staff_name: str | None = None,
        caller_time_phrase: str | None = None,
    ) -> dict:
        # A final confirmation commits the exact previously prepared details. Do not
        # re-parse a newer transcript and accidentally shorten/replace the phone number
        # or revert an explicitly spelled name.
        if confirmed:
            prepared = self.call_session.prepared_booking_payload
            if prepared is None:
                return {
                    "ok": False,
                    "booked": False,
                    "code": "BOOKING_PRECHECK_REQUIRED",
                    "message": "The booking details must be prepared before confirmation.",
                    "instruction": "Collect any missing details, call book_appointment with confirmed=false, read back the returned details, then ask for a fresh yes.",
                }
            public = dict(prepared.get("public") or {})
            fingerprint = str(prepared.get("fingerprint") or "")
            if not self.call_session.booking_is_prepared(fingerprint):
                return {
                    "ok": False,
                    "booked": False,
                    "ready_to_confirm": True,
                    "code": "BOOKING_PRECHECK_REQUIRED",
                    **public,
                }
            if not is_explicit_affirmation(self.call_session.latest_caller_transcript):
                return {
                    "ok": False,
                    "booked": False,
                    "ready_to_confirm": True,
                    "code": "CONFIRMATION_REQUIRED",
                    **public,
                    "instruction": "Ask for an explicit yes to the exact prepared details before booking.",
                }

            try:
                service_id = UUID(str(prepared["service_id"]))
                staff_id = UUID(str(prepared["staff_id"])) if prepared.get("staff_id") else None
                starts_at = prepared["starts_at"]
                assert isinstance(starts_at, datetime)
            except (KeyError, ValueError, TypeError, AssertionError):
                self.call_session.clear_prepared_booking()
                return {"ok": False, "booked": False, "code": "BOOKING_PRECHECK_INVALID"}

            payload = AppointmentCreate(
                customer_name=str(prepared["customer_name"]),
                customer_phone=str(prepared["customer_phone"]),
                service_id=service_id,
                staff_member_id=staff_id,
                starts_at=starts_at,
                idempotency_key=self._idempotency_key(
                    "book",
                    service_id,
                    staff_id if staff_id else "any",
                    starts_at,
                    str(prepared["customer_phone"]),
                ),
                confirmed=True,
                source="voice",
                notes="Created from live voice call",
            )
            try:
                appointment = await self.booking.create(payload, call_id=self.call_session.call_id)
            except HTTPException as exc:
                return self._error_payload(exc)
            self.call_session.clear_prepared_booking()
            self.call_session.set_resolution(
                "booked",
                f"Booked {appointment.customer_name} for {self._local_iso(appointment.starts_at)}.",
            )
            return {
                "ok": True,
                "booked": True,
                "appointment_id": str(appointment.id),
                "status": appointment.status,
                **public,
                "external_event_id": appointment.external_event_id,
                "instruction": "Booking succeeded. Confirm only the exact tool-returned details to the caller.",
            }

        service, service_choices = await self._resolve_service(service_name)
        if service is None:
            return {"ok": False, "code": "SERVICE_NOT_RESOLVED", "choices": service_choices}
        staff, staff_choices = await self._resolve_staff(staff_name)
        if staff_choices is not None:
            return {"ok": False, "code": "STAFF_NOT_RESOLVED", "choices": staff_choices}
        effective_time_phrase = self._effective_caller_time_phrase(caller_time_phrase)
        (
            starts_at,
            datetime_error,
            datetime_source,
        ) = await self._appointment_start_with_business_hours(
            starts_at_local, effective_time_phrase, service.duration_minutes
        )
        if datetime_error:
            return datetime_error
        assert starts_at is not None
        if starts_at <= datetime.now(UTC):
            return {
                "ok": False,
                "code": "TIME_IN_PAST",
                "message": "The requested time is in the past.",
            }
        ends_at = starts_at + timedelta(minutes=service.duration_minutes)
        if not await self._inside_configured_hours(starts_at, ends_at):
            return {
                "ok": False,
                "code": "OUTSIDE_BUSINESS_HOURS",
                "message": "That time is outside the salon's configured business hours.",
            }

        buffered_digits = self.call_session.booking_phone_digits
        if buffered_digits and 9 <= len(buffered_digits) <= 12:
            caller_phone = extract_phone_from_speech(buffered_digits, min_digits=9, max_digits=12)
        else:
            caller_phone = None
            for transcript in reversed(self.call_session.recent_caller_transcripts):
                caller_phone = extract_phone_from_speech(transcript, min_digits=9, max_digits=12)
                if caller_phone is not None:
                    break
        if caller_phone is None:
            return {
                "ok": False,
                "code": "PHONE_NOT_CAPTURED",
                "message": "I could not verify a complete phone number from the caller's own transcript.",
                "instruction": (
                    "Ask the caller to say the phone number again, digit by digit. "
                    "Do not invent or repair missing digits. A complete booking phone should contain at least nine digits."
                ),
            }
        model_phone = extract_phone_from_speech(customer_phone, min_digits=9, max_digits=12)
        phone_corrected = model_phone is None or model_phone.digits != caller_phone.digits

        clean_name = (
            self.call_session.booking_spelled_name or " ".join(customer_name.split()).strip()
        )
        if len(clean_name) < 2:
            return {
                "ok": False,
                "code": "CUSTOMER_NAME_REQUIRED",
                "message": "A customer name is required before booking.",
            }

        service_label = canonical_service_name(service.name)
        display = self._display_local_datetime(starts_at)
        fingerprint = self._idempotency_key(
            "prepare-booking",
            service.id,
            staff.id if staff else "any",
            starts_at,
            clean_name.casefold(),
            caller_phone.digits,
        )
        confirmation_payload = {
            "service": service_label,
            "staff": staff.name if staff else None,
            "customer_name": clean_name,
            "customer_phone": caller_phone.digits,
            "spoken_phone": caller_phone.spoken,
            "phone_corrected_from_caller_transcript": phone_corrected,
            "starts_at": self._local_iso(starts_at),
            "ends_at": self._local_iso(ends_at),
            "datetime_source": datetime_source,
            "meridiem_inferred_from_business_hours": datetime_source
            == "caller_phrase_business_hours_inferred",
            "caller_time_phrase": effective_time_phrase,
            **display,
        }
        prepared_payload: dict[str, object] = {
            "fingerprint": fingerprint,
            "service_id": str(service.id),
            "staff_id": str(staff.id) if staff else None,
            "starts_at": starts_at,
            "customer_name": clean_name,
            "customer_phone": caller_phone.digits,
            "public": confirmation_payload,
        }
        self.call_session.prepare_booking(fingerprint, prepared_payload)
        return {
            "ok": True,
            "booked": False,
            "ready_to_confirm": True,
            "code": "READY_FOR_CONFIRMATION",
            **confirmation_payload,
            "instruction": (
                "Read back the tool-returned service, spoken_start, customer_name and spoken_phone exactly. "
                "Say the phone as individual digits from spoken_phone, then ask 'Is that correct?'. "
                "Only after an explicit caller yes, call book_appointment again with confirmed=true. "
                "The confirmed call commits these prepared details exactly; do not substitute a different phone or name."
            ),
        }

    def _verified_management_phone_from_caller(self) -> str | None:
        """Return a caller-spoken booking phone without promoting fallback callback numbers.

        Before a fallback, a phone spoken in response to the booking-phone request is enough.
        After the caller explicitly says they cannot remember the booking phone, a later number
        is treated as callback-only unless the caller explicitly says they remembered/found the
        booking number. Caller ID and LLM-generated digits are never identity proof.
        """
        if (
            self.call_session.verified_management_phone
            and not self.call_session.voice_state.management_phone_unavailable
        ):
            return self.call_session.verified_management_phone

        if self.call_session.voice_state.management_phone_unavailable:
            latest = self.call_session.latest_caller_transcript or ""
            capture = extract_phone_from_speech(latest)
            if capture is not None and is_management_phone_recovery_statement(latest):
                self.call_session.verify_management_phone(capture.digits)
                self.call_session.update_voice_state(management_phone_unavailable=False)
                return capture.digits
            return None

        capture = phone_from_recent_transcripts(self.call_session.recent_caller_transcripts)
        if capture is not None:
            self.call_session.verify_management_phone(capture.digits)
            return capture.digits
        return None

    def _management_identity_error(self) -> dict:
        phone_unavailable = self.call_session.voice_state.management_phone_unavailable or any(
            is_management_phone_unavailable(text)
            for text in self.call_session.recent_caller_transcripts[-3:]
        )
        if phone_unavailable:
            self.call_session.update_voice_state(management_phone_unavailable=True)
            return {
                "ok": False,
                "code": "IDENTITY_PHONE_UNAVAILABLE",
                "message": "The caller cannot provide the booking phone number, so the appointment cannot be safely disclosed or changed in this call.",
                "identity_verified": False,
                "instruction": (
                    "Do not ask for the booking phone number again unless the caller volunteers that they remember it. "
                    "Do not disclose any appointment details. Explain briefly that you cannot safely cancel or reschedule "
                    "without the booking phone, then offer to take a message for salon staff. Ask for a callback number "
                    "the salon can use; that callback number is for follow-up only and does not verify appointment ownership."
                ),
            }
        return {
            "ok": False,
            "code": "IDENTITY_VERIFICATION_REQUIRED",
            "message": "Please provide the phone number the appointment was booked under.",
            "identity_verified": False,
            "instruction": (
                "Ask once for the phone number used for the booking. Caller ID, a guessed appointment date/time, "
                "or a name alone is not enough to disclose or change an appointment. Do not reveal another "
                "customer's name, service, or appointment time before verification. If the caller says they cannot "
                "remember or provide the booking phone, stop asking for it and offer a staff follow-up message instead."
            ),
        }

    async def find_appointments(
        self,
        customer_phone: str,
        customer_name: str | None = None,
        caller_time_phrase: str | None = None,
    ) -> dict:
        verified_phone = self._verified_management_phone_from_caller()
        if not verified_phone:
            return self._management_identity_error()

        # Keep an explicitly supplied name constraint across follow-up turns. This prevents
        # a caller saying one name, getting no match, then bypassing that mismatch by only
        # supplying a date/time on the next turn.
        effective_name = (
            customer_name or self.call_session.voice_state.customer_name or ""
        ).strip() or None
        rows = await self.appointments.for_customer(
            customer_phone=verified_phone, customer_name=effective_name
        )
        pairs = [(row, await self._appointment_details(row)) for row in rows[:5]]
        rejected_ids = set(self.call_session.rejected_appointment_ids)

        # If the caller now gives a day/date that deterministically matches a previously
        # rejected appointment, treat that as a deliberate re-identification and make
        # the appointment eligible again. This allows "actually, the Tuesday one"
        # without repeatedly surfacing a rejected candidate after a generic "no".
        if caller_time_phrase and rejected_ids:
            reidentified = [
                detail["appointment_id"]
                for row, detail in pairs
                if detail["appointment_id"] in rejected_ids
                and self._appointment_reference_matches(row.starts_at, caller_time_phrase)
            ]
            for appointment_id in reidentified:
                self.call_session.restore_appointment(appointment_id)
                rejected_ids.discard(appointment_id)

        active_pairs = [
            (row, detail) for row, detail in pairs if detail["appointment_id"] not in rejected_ids
        ]
        rejected_details = [
            detail for _row, detail in pairs if detail["appointment_id"] in rejected_ids
        ]

        if caller_time_phrase and pairs:
            matched_pairs = [
                (row, detail)
                for row, detail in active_pairs
                if self._appointment_reference_matches(row.starts_at, caller_time_phrase)
            ]
            if matched_pairs:
                active_pairs = matched_pairs
            else:
                return {
                    "ok": False,
                    "code": "APPOINTMENT_REFERENCE_MISMATCH",
                    "message": "No eligible appointment matches the caller's stated day/date.",
                    "caller_time_phrase": caller_time_phrase,
                    "identity_verified": True,
                    "verified_customer_phone": verified_phone,
                    "appointments": [detail for _row, detail in active_pairs],
                    "rejected_appointments": rejected_details,
                    "instruction": (
                        "State that no appointment matches the caller's stated day/date. If rejected_appointments "
                        "is non-empty, say those were already rejected by the caller; do not ask whether the same "
                        "rejected appointment is the one again. Mention any other appointments only as alternatives "
                        "on file, using their exact tool-returned service and spoken_start."
                    ),
                }

        details = [detail for _row, detail in active_pairs]
        if not details and rejected_details:
            return {
                "ok": False,
                "code": "ONLY_REJECTED_APPOINTMENTS_REMAIN",
                "message": "The only appointments found were already rejected by the caller.",
                "identity_verified": True,
                "verified_customer_phone": verified_phone,
                "appointments": [],
                "rejected_appointments": rejected_details,
                "instruction": (
                    "Do not ask whether a rejected appointment is the one again. Say you cannot find another "
                    "appointment for the lookup details. If the caller changes their mind, ask them to identify "
                    "the rejected appointment by its actual returned day/date before looking it up again."
                ),
            }
        return {
            "ok": True,
            "identity_verified": True,
            "verified_customer_phone": verified_phone,
            "appointments": details,
            "rejected_appointments": rejected_details,
            "instruction": (
                "Use each appointment's tool-returned service and spoken_start exactly. Never replace them with "
                "the caller's guessed day/date. Never re-present a rejected appointment as the selected candidate "
                "unless the caller later re-identifies it by its actual returned day/date. For rescheduling, keep "
                "the existing service unless the caller explicitly asks to change the service."
            ),
        }

    async def reschedule_appointment(
        self,
        appointment_id: str,
        starts_at_local: str,
        confirmed: bool,
        caller_time_phrase: str | None = None,
    ) -> dict:
        verified_phone = self._verified_management_phone_from_caller()
        if not verified_phone:
            return self._management_identity_error()
        try:
            appointment_uuid = UUID(appointment_id)
        except ValueError:
            return {
                "ok": False,
                "code": "INVALID_APPOINTMENT_ID",
                "message": "Appointment ID is invalid.",
            }
        existing = await self.appointments.get(appointment_uuid)
        if existing is None:
            return {
                "ok": False,
                "code": "APPOINTMENT_NOT_FOUND",
                "message": "Appointment not found.",
            }
        if existing.customer_phone != verified_phone:
            return {
                "ok": False,
                "code": "APPOINTMENT_OWNERSHIP_MISMATCH",
                "message": "That appointment is not associated with the verified booking phone number.",
                "identity_verified": True,
                "instruction": "Do not reveal appointment details. Ask the caller to verify the booking phone number.",
            }
        service = await self.services.get(existing.service_id)
        if service is None:
            return {
                "ok": False,
                "code": "SERVICE_NOT_FOUND",
                "message": "Appointment service is unavailable.",
            }

        effective_time_phrase = self._effective_caller_time_phrase(caller_time_phrase)
        (
            starts_at,
            datetime_error,
            datetime_source,
        ) = await self._appointment_start_with_business_hours(
            starts_at_local, effective_time_phrase, service.duration_minutes
        )
        if datetime_error:
            return datetime_error
        assert starts_at is not None
        fingerprint = self._idempotency_key("prepare-reschedule", existing.id, starts_at)

        if not confirmed:
            return await self.check_reschedule_availability(
                str(existing.id), starts_at_local, effective_time_phrase
            )

        old_display = self._display_local_datetime(existing.starts_at)
        new_display = self._display_local_datetime(starts_at)
        if not self.call_session.reschedule_is_prepared(fingerprint):
            return {
                "ok": False,
                "code": "RESCHEDULE_PRECHECK_REQUIRED",
                "appointment_id": str(existing.id),
                "service": canonical_service_name(service.name),
                "current_spoken_start": old_display["spoken_start"],
                "requested_spoken_start": new_display["spoken_start"],
                "instruction": (
                    "The exact reschedule was not prepared before confirmation. Check availability for this "
                    "appointment/time, read back the returned current and requested times, and ask again."
                ),
            }
        if not is_explicit_affirmation(self.call_session.latest_caller_transcript):
            return {
                "ok": False,
                "code": "CONFIRMATION_REQUIRED",
                "appointment_id": str(existing.id),
                "service": canonical_service_name(service.name),
                "current_spoken_start": old_display["spoken_start"],
                "requested_spoken_start": new_display["spoken_start"],
                "instruction": "Ask for an explicit yes to the exact prepared reschedule details.",
            }

        ends_at = starts_at + timedelta(minutes=service.duration_minutes)
        if not await self._inside_configured_hours(starts_at, ends_at):
            return {
                "ok": False,
                "code": "OUTSIDE_BUSINESS_HOURS",
                "message": "That time is outside the salon's configured business hours.",
            }
        payload = AppointmentMove(
            starts_at=starts_at,
            idempotency_key=self._idempotency_key("reschedule", appointment_uuid, starts_at),
            confirmed=True,
        )
        try:
            appointment = await self.booking.reschedule(appointment_uuid, payload)
        except HTTPException as exc:
            return self._error_payload(exc)
        self.call_session.clear_prepared_reschedule()
        self.call_session.set_resolution(
            "appointment_changed",
            f"Rescheduled appointment {appointment.id} to {self._local_iso(appointment.starts_at)}.",
        )
        return {
            "ok": True,
            "rescheduled": True,
            "appointment_id": str(appointment.id),
            "status": appointment.status,
            "service": canonical_service_name(service.name),
            "current_spoken_start": old_display["spoken_start"],
            "starts_at": self._local_iso(appointment.starts_at),
            "ends_at": self._local_iso(appointment.ends_at),
            "requested_spoken_start": self._display_local_datetime(appointment.starts_at)[
                "spoken_start"
            ],
            "spoken_start": self._display_local_datetime(appointment.starts_at)["spoken_start"],
            "datetime_source": datetime_source,
            "caller_time_phrase": effective_time_phrase,
            "instruction": (
                "Reschedule succeeded. Say the appointment was rescheduled/moved, not booked, and use the "
                "tool-returned service and spoken_start exactly."
            ),
        }

    async def cancel_appointment(self, appointment_id: str, confirmed: bool) -> dict:
        verified_phone = self._verified_management_phone_from_caller()
        if not verified_phone:
            return self._management_identity_error()
        try:
            appointment_uuid = UUID(appointment_id)
        except ValueError:
            return {
                "ok": False,
                "code": "INVALID_APPOINTMENT_ID",
                "message": "Appointment ID is invalid.",
            }
        existing = await self.appointments.get(appointment_uuid)
        if existing is None:
            return {
                "ok": False,
                "code": "APPOINTMENT_NOT_FOUND",
                "message": "Appointment not found.",
            }
        if existing.customer_phone != verified_phone:
            return {
                "ok": False,
                "code": "APPOINTMENT_OWNERSHIP_MISMATCH",
                "message": "That appointment is not associated with the verified booking phone number.",
                "identity_verified": True,
                "instruction": "Do not reveal appointment details. Ask the caller to verify the booking phone number.",
            }
        service = await self.services.get(existing.service_id)
        details = await self._appointment_details(existing)
        fingerprint = self._idempotency_key("prepare-cancel", existing.id, existing.starts_at)

        if existing.status == "cancelled":
            return {
                "ok": True,
                "cancelled": True,
                "already_cancelled": True,
                **details,
                "instruction": "The appointment was already cancelled. Do not claim you cancelled it again.",
            }

        if not confirmed:
            self.call_session.prepare_cancel(fingerprint)
            return {
                "ok": True,
                "cancelled": False,
                "ready_to_confirm": True,
                **details,
                "instruction": (
                    "Read back the exact tool-returned service and spoken_start and ask one direct cancellation "
                    "confirmation question. Only after a new explicit yes may you call cancel_appointment again "
                    "with confirmed=true."
                ),
            }

        if not self.call_session.cancel_is_prepared(fingerprint):
            return {
                "ok": False,
                "code": "CANCEL_PRECHECK_REQUIRED",
                **details,
                "instruction": (
                    "This cancellation was not prepared for confirmation. Call cancel_appointment with "
                    "confirmed=false first, read back the exact appointment details, and ask again."
                ),
            }
        if not is_explicit_affirmation(self.call_session.latest_caller_transcript):
            return {
                "ok": False,
                "code": "CONFIRMATION_REQUIRED",
                **details,
                "instruction": "Ask for a fresh explicit yes to cancelling this exact appointment.",
            }

        try:
            appointment = await self.booking.cancel(appointment_uuid, confirmed=True)
        except HTTPException as exc:
            return self._error_payload(exc)
        self.call_session.clear_prepared_cancel()
        self.call_session.set_resolution(
            "cancelled",
            f"Cancelled appointment {appointment.id} after explicit caller confirmation.",
        )
        return {
            "ok": True,
            "cancelled": True,
            "appointment_id": str(appointment.id),
            "status": appointment.status,
            "service": canonical_service_name(service.name) if service else details.get("service"),
            "spoken_start": details.get("spoken_start"),
            "starts_at": details.get("starts_at"),
            "customer_name": details.get("customer_name"),
            "instruction": (
                "Cancellation succeeded. Confirm only the exact tool-returned service and spoken_start. "
                "Do not say the appointment was rescheduled or booked."
            ),
        }

    async def take_message(
        self,
        message: str,
        customer_phone: str,
        customer_name: str | None = None,
        confirmed: bool = False,
        target_staff: str | None = None,
    ) -> dict:
        """Prepare or submit a caller message with trusted confirmation state.

        ``confirmed=False`` never persists the message. It stores an exact prepared
        payload in the live session and returns caller-safe readback fields. A later
        ``confirmed=True`` can submit only that exact prepared payload and only when
        the latest finalized caller turn is an explicit affirmation.
        """

        if confirmed:
            prepared = self.call_session.prepared_message_payload
            if prepared is None:
                return {
                    "ok": False,
                    "submitted": False,
                    "code": "MESSAGE_PRECHECK_REQUIRED",
                    "instruction": (
                        "Prepare the exact message and callback number first, read them back, "
                        "then ask for a fresh explicit yes."
                    ),
                }
            fingerprint = str(prepared.get("fingerprint") or "")
            if not self.call_session.message_is_prepared(fingerprint):
                return {
                    "ok": False,
                    "submitted": False,
                    "code": "MESSAGE_PRECHECK_REQUIRED",
                }
            if not is_explicit_affirmation(self.call_session.latest_caller_transcript):
                return {
                    "ok": False,
                    "submitted": False,
                    "ready_to_confirm": True,
                    "code": "CONFIRMATION_REQUIRED",
                    "message": str(prepared.get("message") or ""),
                    "customer_phone": str(prepared.get("customer_phone") or ""),
                    "customer_name": str(prepared.get("customer_name") or "Caller"),
                    "target_staff": prepared.get("target_staff"),
                }

            safe_message = str(prepared.get("message") or "").strip()[:500]
            callback_phone = str(prepared.get("customer_phone") or "").strip()[:32]
            name = str(prepared.get("customer_name") or "Caller").strip()[:160] or "Caller"
            target = str(prepared.get("target_staff") or "").strip()[:160] or None
            stored_text = f"For {target}: {safe_message}" if target else safe_message
            now = utcnow()

            if self.call_session.call_id is not None:
                repo = MessageRepository(self.db, self.tenant.id)
                stored = await repo.by_call(self.call_session.call_id)
                if stored is None:
                    stored = SalonMessage(
                        tenant_id=self.tenant.id,
                        call_id=self.call_session.call_id,
                        customer_name=name,
                        callback_phone=callback_phone or None,
                        message_text=stored_text,
                        status="new",
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(stored)
                else:
                    stored.customer_name = name
                    stored.callback_phone = callback_phone or None
                    stored.message_text = stored_text
                    stored.updated_at = now
                await self.db.commit()

            target_summary = f" for {target}" if target else ""
            self.call_session.clear_prepared_message()
            self.call_session.set_resolution(
                "message_taken",
                f"Message taken{target_summary} from {name}: {safe_message}",
                follow_up_required=True,
                follow_up_notes=f"Follow up with {name} on {callback_phone}.",
            )
            return {
                "ok": True,
                "submitted": True,
                "outcome": "message_taken",
                "customer_name": name,
                "customer_phone": callback_phone,
                "message": safe_message,
                "target_staff": target,
                "message_status": "new",
            }

        safe_message = " ".join(str(message or "").split()).strip()[:500]
        callback_phone = "".join(ch for ch in str(customer_phone or "") if ch.isdigit())
        name = " ".join(str(customer_name or "Caller").split()).strip()[:160] or "Caller"
        target = " ".join(str(target_staff or "").split()).strip()[:160] or None
        if len(safe_message) < 2:
            return {"ok": False, "code": "MESSAGE_REQUIRED", "message": "A message is required."}
        if not (9 <= len(callback_phone) <= 12):
            return {
                "ok": False,
                "code": "CALLBACK_PHONE_REQUIRED",
                "message": "A complete callback phone number is required.",
            }

        fingerprint = self._idempotency_key(
            "prepare-message",
            target or "salon",
            safe_message.casefold(),
            callback_phone,
            name.casefold(),
        )
        prepared_payload: dict[str, object] = {
            "fingerprint": fingerprint,
            "message": safe_message,
            "customer_phone": callback_phone,
            "customer_name": name,
            "target_staff": target,
        }
        self.call_session.prepare_message(fingerprint, prepared_payload)
        return {
            "ok": True,
            "submitted": False,
            "ready_to_confirm": True,
            "message": safe_message,
            "customer_phone": callback_phone,
            "spoken_phone": speak_phone_digits(callback_phone),
            "customer_name": name,
            "target_staff": target,
            "instruction": (
                "Read back the exact message, target if present, and callback number. "
                "Only after a fresh explicit yes may take_message be called again with confirmed=true."
            ),
        }
