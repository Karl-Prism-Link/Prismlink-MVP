import pytest
from prometheus_client import REGISTRY

from monitoring import db_timer, record_call_finished, record_call_started, record_voice_turn_latency


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



def test_live_call_metrics_are_bounded_and_record_latency():
    starts_before = REGISTRY.get_sample_value("calls_started_total") or 0
    completed_before = REGISTRY.get_sample_value("calls_completed_total") or 0
    active_before = REGISTRY.get_sample_value("active_calls") or 0
    fallback_before = REGISTRY.get_sample_value("call_outcomes_total", {"outcome": "fallback"}) or 0
    latency_before = REGISTRY.get_sample_value("voice_turn_latency_seconds_count") or 0

    record_call_started()
    record_voice_turn_latency(1.25)
    record_call_finished("failed_fallback", 3.0)

    assert REGISTRY.get_sample_value("calls_started_total") == starts_before + 1
    assert REGISTRY.get_sample_value("calls_completed_total") == completed_before + 1
    assert REGISTRY.get_sample_value("active_calls") == active_before
    assert REGISTRY.get_sample_value("call_outcomes_total", {"outcome": "fallback"}) == fallback_before + 1
    assert REGISTRY.get_sample_value("voice_turn_latency_seconds_count") == latency_before + 1
