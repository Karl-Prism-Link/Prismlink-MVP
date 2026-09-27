from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from apps.api.middleware import RequestLoggingMiddleware
from apps.api.routes import appointments, auth, calendar, calls, health, messages, salon, voice
from core.config import get_settings, validate_runtime_settings
from database.models import Base
from database.session import engine
from monitoring import setup_metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    validate_runtime_settings(settings)
    # Local development and tests remain zero-setup. Production must run Alembic first.
    if settings.app_env != "production":
        Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="PRISM LINK Salon-First MVP",
    version="0.4.10",
    lifespan=lifespan,
)
setup_metrics(app)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_base_url, "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(salon.router)
app.include_router(calendar.router)
app.include_router(appointments.router)
app.include_router(calls.router)
app.include_router(messages.router)
app.include_router(voice.router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parents[1] / "dashboard" / "index.html")
