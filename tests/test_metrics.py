"""Metrics endpoint tests: counters, latency, and prediction distribution.

prometheus-client keeps one process-global registry, so these tests assert
DELTAS around known requests rather than absolute values — other tests in
the session have already incremented the counters.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.serve import app


def _metric_value(text: str, name: str, labels: str = "") -> float:
    pattern = re.escape(name) + (re.escape(labels) if labels else r"(?:\{[^}]*\})?")
    total = 0.0
    for line in text.splitlines():
        m = re.match(rf"^{pattern} ([0-9.e+-]+)$", line)
        if m:
            total += float(m.group(1))
    return total


def test_predict_requests_are_counted_and_timed(client):
    before = client.get("/metrics").text
    n_before = _metric_value(
        before, "http_requests_total", '{method="POST",path="/predict",status="200"}'
    )

    assert client.post("/predict", json={"month": "2024-07"}).status_code == 200
    after = client.get("/metrics").text
    n_after = _metric_value(
        after, "http_requests_total", '{method="POST",path="/predict",status="200"}'
    )
    assert n_after == n_before + 1

    latency_count = _metric_value(
        after, "http_request_duration_seconds_count", '{path="/predict"}'
    )
    assert latency_count >= n_after


def test_prediction_distribution_is_observed(client):
    before = client.get("/metrics").text
    c_before = _metric_value(before, "predicted_volume_moves_count")
    s_before = _metric_value(before, "predicted_volume_moves_sum")

    july = client.post("/predict", json={"month": "2024-07"}).json()

    after = client.get("/metrics").text
    c_after = _metric_value(after, "predicted_volume_moves_count")
    s_after = _metric_value(after, "predicted_volume_moves_sum")
    assert c_after == c_before + 1
    # the histogram observed the same magnitude the API returned
    assert s_after - s_before == pytest.approx(july["predicted_volume"], abs=1)


def test_rejected_requests_are_labeled_by_status(client):
    before = client.get("/metrics").text
    n_before = _metric_value(
        before, "http_requests_total", '{method="POST",path="/predict",status="400"}'
    )
    client.post("/predict", json={"month": "2026-06"})
    after = client.get("/metrics").text
    n_after = _metric_value(
        after, "http_requests_total", '{method="POST",path="/predict",status="400"}'
    )
    assert n_after == n_before + 1


def test_unknown_paths_share_one_label(client):
    client.get("/does-not-exist")
    text = client.get("/metrics").text
    assert 'path="other"' in text
    assert "/does-not-exist" not in text


def test_unknown_methods_share_one_label(client):
    label = '{method="other",path="/health",status="405"}'
    before = client.get("/metrics").text
    for method in ("CUSTOMONE", "CUSTOMTWO", "CUSTOMTHREE"):
        assert client.request(method, "/health").status_code == 405
    after = client.get("/metrics").text
    assert _metric_value(after, "http_requests_total", label) == (
        _metric_value(before, "http_requests_total", label) + 3
    )
    assert not any(
        method in after for method in ("CUSTOMONE", "CUSTOMTWO", "CUSTOMTHREE")
    )


def test_metrics_prefix_lookalike_is_counted_as_unknown_path(client):
    label = '{method="GET",path="other",status="404"}'
    before = client.get("/metrics").text
    assert client.get("/metrics-missing").status_code == 404
    after = client.get("/metrics").text
    assert _metric_value(after, "http_requests_total", label) == (
        _metric_value(before, "http_requests_total", label) + 1
    )


def test_scrapes_do_not_record_themselves(client):
    before = _metric_value(client.get("/metrics").text, "http_requests_total")
    client.get("/metrics/")
    after = _metric_value(client.get("/metrics").text, "http_requests_total")
    assert after == before


@pytest.mark.parametrize(
    "prediction", [float("nan"), float("inf"), -float("inf"), -1.0]
)
def test_invalid_forecasts_do_not_poison_prediction_metrics(
    client, monkeypatch, prediction
):
    monkeypatch.setattr(app.state.artifact["model"], "predict", lambda _: [prediction])
    before = client.get("/metrics").text
    label = '{method="POST",path="/predict",status="500"}'

    response = client.post("/predict", json={"month": "2024-07"})

    assert response.status_code == 500
    assert response.json()["detail"] == "model produced an invalid forecast"
    after = client.get("/metrics").text
    for metric in ("predicted_volume_moves_count", "predicted_volume_moves_sum"):
        assert _metric_value(after, metric) == _metric_value(before, metric)
    assert _metric_value(after, "http_requests_total", label) == (
        _metric_value(before, "http_requests_total", label) + 1
    )


def test_zero_forecast_is_valid(client, monkeypatch):
    monkeypatch.setattr(app.state.artifact["model"], "predict", lambda _: [0.0])
    response = client.post("/predict", json={"month": "2024-07"})
    assert response.status_code == 200
    assert response.json()["predicted_volume"] == 0


def test_unhandled_errors_are_counted_as_500(client):
    """An exception that never becomes a response must still be counted —
    otherwise outages are invisible on the error-rate panel."""
    with TestClient(app, raise_server_exceptions=False) as c:
        good = app.state.artifact
        label = '{method="POST",path="/predict",status="500"}'
        before = _metric_value(c.get("/metrics").text, "http_requests_total", label)
        try:
            app.state.artifact = dict(good, model=None)  # forces a 500
            assert c.post("/predict", json={"month": "2024-07"}).status_code == 500
        finally:
            app.state.artifact = good
        after = _metric_value(c.get("/metrics").text, "http_requests_total", label)
        assert after == before + 1
