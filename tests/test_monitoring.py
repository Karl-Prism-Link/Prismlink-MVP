import pytest
from prometheus_client import REGISTRY

from monitoring import db_timer


def test_metrics_endpoint(client):
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "calls_started_total" in response.text
    assert "db_latency_seconds" in response.text


def test_db_timer_records_latency_and_errors():
    error_before = REGISTRY.get_sample_value(
        "db_errors_total", {"operation": "execute"}
    ) or 0
    latency_before = REGISTRY.get_sample_value(
        "db_latency_seconds_count", {"operation": "execute"}
    ) or 0

    with pytest.raises(RuntimeError), db_timer("execute"):
        raise RuntimeError("database failure")

    assert REGISTRY.get_sample_value(
        "db_errors_total", {"operation": "execute"}
    ) == error_before + 1
    assert (
        REGISTRY.get_sample_value("db_latency_seconds_count", {"operation": "execute"})
        == latency_before + 1
    )
