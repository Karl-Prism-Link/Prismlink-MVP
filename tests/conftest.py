import os
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./test_prism_link.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough"
os.environ["CALENDAR_PROVIDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from database.session import engine


@pytest.fixture(scope="session", autouse=True)
def clean_test_database():
    path = Path("test_prism_link.db")
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    yield
    engine.dispose()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
