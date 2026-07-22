"""Metrics endpoint tests: counters, latency, and prediction distribution.

prometheus-client keeps one process-global registry, so these tests assert
DELTAS around known requests rather than absolute values — other tests in
the session have already incremented the counters.
"""
import re

import pytest

from tests.test_api import client  # reuse the artifact-building fixture  # noqa: F401


def _metric_value(text: str, name: str, labels: str = "") -> float:
    pattern = re.escape(name) + (re.escape(labels) if labels else r"(?:\{[^}]*\})?")
    total = 0.0
    for line in text.splitlines():
        m = re.match(rf"^{pattern} ([0-9.e+-]+)$", line)
        if m:
            total += float(m.group(1))
    return total


def test_predict_requests_are_counted_and_timed(client):  # noqa: F811
    before = client.get("/metrics").text
    n_before = _metric_value(
        before, "http_requests_total",
        '{method="POST",path="/predict",status="200"}')

    assert client.post("/predict", json={"month": "2024-07"}).status_code == 200
    after = client.get("/metrics").text
    n_after = _metric_value(
        after, "http_requests_total",
        '{method="POST",path="/predict",status="200"}')
    assert n_after == n_before + 1

    latency_count = _metric_value(
        after, "http_request_duration_seconds_count", '{path="/predict"}')
    assert latency_count >= n_after


def test_prediction_distribution_is_observed(client):  # noqa: F811
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


def test_rejected_requests_are_labeled_by_status(client):  # noqa: F811
    before = client.get("/metrics").text
    n_before = _metric_value(
        before, "http_requests_total",
        '{method="POST",path="/predict",status="400"}')
    client.post("/predict", json={"month": "2026-06"})
    after = client.get("/metrics").text
    n_after = _metric_value(
        after, "http_requests_total",
        '{method="POST",path="/predict",status="400"}')
    assert n_after == n_before + 1


def test_unknown_paths_share_one_label(client):  # noqa: F811
    client.get("/does-not-exist")
    text = client.get("/metrics").text
    assert 'path="other"' in text
    assert "/does-not-exist" not in text


def test_unhandled_errors_are_counted_as_500(client):  # noqa: F811
    """An exception that never becomes a response must still be counted —
    otherwise outages are invisible on the error-rate panel."""
    from fastapi.testclient import TestClient

    from src.serve import app

    with TestClient(app, raise_server_exceptions=False) as c:
        good = app.state.artifact
        label = '{method="POST",path="/predict",status="500"}'
        before = _metric_value(
            c.get("/metrics").text, "http_requests_total", label)
        try:
            app.state.artifact = dict(good, model=None)  # forces a 500
            assert c.post("/predict",
                          json={"month": "2024-07"}).status_code == 500
        finally:
            app.state.artifact = good
        after = _metric_value(
            c.get("/metrics").text, "http_requests_total", label)
        assert after == before + 1
