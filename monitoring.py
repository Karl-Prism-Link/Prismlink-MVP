from contextlib import contextmanager
from time import perf_counter

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

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
HTTP_REQUESTS = Counter(
    "http_requests", "HTTP requests by method, route template and status", ["method", "route", "status"]
)
HTTP_REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


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
        service = "deepgram" if stage_name in {"stt", "tts"} else "groq" if stage_name == "llm" else None
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
            HTTP_REQUEST_LATENCY.labels(request.method, route_label).observe(perf_counter() - started)
            raise
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_label, str(response.status_code)).inc()
        HTTP_REQUEST_LATENCY.labels(request.method, route_label).observe(perf_counter() - started)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
