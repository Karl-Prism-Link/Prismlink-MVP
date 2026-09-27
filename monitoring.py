from contextlib import contextmanager
from time import perf_counter

from fastapi import Response
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
    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
