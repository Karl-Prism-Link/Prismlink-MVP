import hmac
from contextlib import contextmanager
from time import perf_counter

from fastapi import HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from core.config import get_settings

CALLS_STARTED = Counter("calls_started", "Calls started")
CALLS_COMPLETED = Counter("calls_completed", "Calls completed")
CALLS_FAILED = Counter("calls_failed", "Calls failed")
STG_LATENCY = Histogram(
    "stg_latency_seconds",
    "Pipeline stage latency",
    ["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
API_ERRORS = Counter("api_errors", "External API errors", ["service"])
ACTIVE_CALLS = Gauge("active_calls", "Calls currently active")
DB_LATENCY = Histogram(
    "db_latency_seconds",
    "Database operation latency",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
DB_ERRORS = Counter("db_errors", "Database operation errors", ["operation"])
CALL_OUTCOMES = Counter("call_outcomes", "Completed live calls by bounded outcome", ["outcome"])
VOICE_TURN_LATENCY = Histogram(
    "voice_turn_latency_seconds",
    "Caller end-of-turn to first Pipecat response audio",
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 6, 10),
)
GROQ_REQUESTS = Counter(
    "groq_requests_total",
    "Groq chat completion requests by bounded model tier and outcome",
    ["model_tier", "outcome"],
)
GROQ_PROMPT_TOKENS = Counter(
    "groq_prompt_tokens_total", "Actual prompt tokens reported by Groq", ["model_tier"]
)
GROQ_COMPLETION_TOKENS = Counter(
    "groq_completion_tokens_total", "Actual completion tokens reported by Groq", ["model_tier"]
)
GROQ_TOTAL_TOKENS = Counter(
    "groq_total_tokens_total", "Actual total tokens reported by Groq", ["model_tier"]
)
GROQ_RATE_LIMIT_TOKENS = Gauge(
    "groq_rate_limit_tokens",
    "Latest Groq per-minute token limit and remaining budget from response headers",
    ["model_tier", "kind"],
)
GROQ_RATE_LIMIT_TOKEN_RESET = Gauge(
    "groq_rate_limit_token_reset_seconds",
    "Seconds until the Groq per-minute token limit resets",
    ["model_tier"],
)
GROQ_RATE_LIMIT_REQUESTS = Gauge(
    "groq_rate_limit_requests",
    "Latest Groq daily request limit and remaining requests from response headers",
    ["model_tier", "kind"],
)
# Booking metrics deliberately use a small, fixed label set. Never attach tenant IDs,
# caller numbers, customer names, appointment IDs, or arbitrary exception text.
BOOKING_ATTEMPTS = Counter("booking_attempts", "Confirmed appointment creation attempts")
BOOKING_CREATED = Counter("booking_created", "New appointments successfully persisted")
BOOKING_IDEMPOTENT_REPLAYS = Counter(
    "booking_idempotent_replays", "Booking requests safely returned an existing appointment"
)
BOOKING_FAILURES = Counter(
    "booking_failures", "Appointment creation failures by bounded reason", ["reason"]
)
BOOKING_AVAILABILITY_CHECKS = Counter(
    "booking_availability_checks",
    "Availability checks by result",
    ["result"],
)
HTTP_REQUESTS = Counter(
    "http_requests",
    "HTTP requests by method, route template and status",
    ["method", "route", "status"],
)
HTTP_REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


def booking_failure_reason(exc):
    """Map errors to bounded metric labels; never expose arbitrary exception strings."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else None
        known = {
            "CONFIRMATION_REQUIRED": "confirmation_required",
            "APPOINTMENT_CONFLICT": "appointment_conflict",
            "CALENDAR_UNAVAILABLE": "calendar_unavailable",
            "OUTSIDE_BUSINESS_HOURS": "outside_business_hours",
            "APPOINTMENT_IN_PAST": "appointment_in_past",
            "DATETIME_TIMEZONE_REQUIRED": "timezone_required",
            "INVALID_SALON_TIMEZONE": "invalid_salon_timezone",
        }
        if code in known:
            return known[code]
        if exc.status_code == 404:
            return "not_found"
        if exc.status_code == 422:
            return "validation_error"
        if exc.status_code == 409:
            return "conflict"
    return "internal_error"


def record_call_started():
    CALLS_STARTED.inc()
    ACTIVE_CALLS.inc()


def record_call_finished(outcome, duration_seconds):
    ACTIVE_CALLS.dec()
    CALLS_COMPLETED.inc()
    value = str(outcome or "").casefold()
    if value == "failed_fallback":
        label = "fallback"
        CALLS_FAILED.inc()
    elif value in {"booked", "rescheduled", "cancelled"}:
        label = "appointment"
    elif value in {"message_taken", "message_recorded"}:
        label = "message"
    elif value in {"resolved", "enquiry_resolved"}:
        label = "resolved"
    elif value in {"caller_abandoned", "abandoned"}:
        label = "abandoned"
    else:
        label = "other"
    CALL_OUTCOMES.labels(outcome=label).inc()
    STG_LATENCY.labels(stage="total_call").observe(max(0.0, float(duration_seconds)))


def parse_groq_rate_limit_headers(headers):
    """Extract only documented, non-sensitive Groq rate-limit response headers."""
    headers = headers or {}

    def integer(name):
        value = headers.get(name)
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    def duration_seconds(value):
        if not value:
            return None
        import re

        text = str(value).strip().lower()
        matches = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
        if not matches or "".join(number + unit for number, unit in matches) != text:
            return None
        scales = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
        return sum(float(number) * scales[unit] for number, unit in matches)

    return {
        "token_limit": integer("x-ratelimit-limit-tokens"),
        "token_remaining": integer("x-ratelimit-remaining-tokens"),
        "token_reset_seconds": duration_seconds(headers.get("x-ratelimit-reset-tokens")),
        "request_limit": integer("x-ratelimit-limit-requests"),
        "request_remaining": integer("x-ratelimit-remaining-requests"),
        "request_reset": headers.get("x-ratelimit-reset-requests"),
        "retry_after": headers.get("retry-after"),
    }


def record_groq_response(model_tier, usage, headers):
    """Record actual Groq usage and its latest rate-limit headers."""
    tier = model_tier if model_tier in {"fast", "reasoning"} else "other"
    GROQ_REQUESTS.labels(model_tier=tier, outcome="success").inc()
    prompt = getattr(usage, "prompt_tokens", None) if usage is not None else None
    completion = getattr(usage, "completion_tokens", None) if usage is not None else None
    total = getattr(usage, "total_tokens", None) if usage is not None else None
    if prompt is not None:
        GROQ_PROMPT_TOKENS.labels(model_tier=tier).inc(max(0, int(prompt)))
    if completion is not None:
        GROQ_COMPLETION_TOKENS.labels(model_tier=tier).inc(max(0, int(completion)))
    if total is not None:
        GROQ_TOTAL_TOKENS.labels(model_tier=tier).inc(max(0, int(total)))

    snapshot = parse_groq_rate_limit_headers(headers)
    _record_groq_rate_limit_metrics(tier, snapshot)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        **snapshot,
    }


def _record_groq_rate_limit_metrics(tier, snapshot):
    for kind, key in (("limit", "token_limit"), ("remaining", "token_remaining")):
        value = snapshot[key]
        if value is not None:
            GROQ_RATE_LIMIT_TOKENS.labels(model_tier=tier, kind=kind).set(value)
    if snapshot["token_reset_seconds"] is not None:
        GROQ_RATE_LIMIT_TOKEN_RESET.labels(model_tier=tier).set(snapshot["token_reset_seconds"])
    for kind, key in (("limit", "request_limit"), ("remaining", "request_remaining")):
        value = snapshot[key]
        if value is not None:
            GROQ_RATE_LIMIT_REQUESTS.labels(model_tier=tier, kind=kind).set(value)


def record_groq_error(model_tier, status_code, headers=None):
    """Record bounded Groq failures and any rate-limit metadata on the error response."""
    tier = model_tier if model_tier in {"fast", "reasoning"} else "other"
    outcome = (
        "http_429"
        if status_code == 429
        else "transport_error"
        if status_code is None
        else "http_error"
    )
    GROQ_REQUESTS.labels(model_tier=tier, outcome=outcome).inc()
    snapshot = parse_groq_rate_limit_headers(headers)
    _record_groq_rate_limit_metrics(tier, snapshot)
    return snapshot


def record_voice_turn_latency(seconds):
    if seconds >= 0:
        VOICE_TURN_LATENCY.observe(seconds)


@contextmanager
def call_scope():
    CALLS_STARTED.inc()
    ACTIVE_CALLS.inc()
    started = perf_counter()
    try:
        yield
    except Exception:
        CALLS_FAILED.inc()
        raise
    else:
        CALLS_COMPLETED.inc()
    finally:
        STG_LATENCY.labels(stage="total_call").observe(perf_counter() - started)
        ACTIVE_CALLS.dec()


@contextmanager
def stage_timer(stage_name):
    if stage_name not in {"stt", "router", "llm", "tts"}:
        raise ValueError(f"Invalid stage: {stage_name}")
    started = perf_counter()
    try:
        yield
    except Exception:
        service = (
            "deepgram" if stage_name in {"stt", "tts"} else "groq" if stage_name == "llm" else None
        )
        if service:
            API_ERRORS.labels(service=service).inc()
        raise
    finally:
        STG_LATENCY.labels(stage=stage_name).observe(perf_counter() - started)


@contextmanager
def db_timer(operation):
    if operation not in {"execute", "get", "commit", "rollback", "flush", "refresh"}:
        raise ValueError(f"Invalid DB operation: {operation}")
    started = perf_counter()
    try:
        yield
    except Exception:
        DB_ERRORS.labels(operation=operation).inc()
        raise
    finally:
        DB_LATENCY.labels(operation=operation).observe(perf_counter() - started)


def setup_metrics(app):
    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = request.scope.get("route")
            route_label = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route_label, "500").inc()
            HTTP_REQUEST_LATENCY.labels(request.method, route_label).observe(
                perf_counter() - started
            )
            raise
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_label, str(response.status_code)).inc()
        HTTP_REQUEST_LATENCY.labels(request.method, route_label).observe(perf_counter() - started)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        settings = get_settings()
        token = settings.metrics_token
        if token:
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {token}".encode()):
                raise HTTPException(status_code=401, detail="Invalid metrics credentials")
        elif settings.app_env == "production":
            # Fail closed: never expose process metrics unauthenticated in production.
            raise HTTPException(status_code=404, detail="Not found")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
