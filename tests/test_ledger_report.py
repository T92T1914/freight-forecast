"""The reading view does not turn fixtures or unknown outcomes into live evidence."""

import json
from copy import deepcopy

from tools.presentation import ROOT
from tools.render_ledger_report import render_outputs


def test_report_preserves_categories_and_download_bytes():
    output = render_outputs()
    page = output["ledger.html"].decode()
    assert "0 real issued predictions" in page
    assert "1 retrospective replays" in page
    assert "3 synthetic workflow fixtures" in page
    assert "Unavailable is not zero" in page
    assert "first_release" in page
    assert "last observation beat the fitted model" in page
    assert "independent timestamping" in page
    assert output["ledger.json"] == (ROOT / "docs/ledger-fixture.json").read_bytes()
    assert "Available through" in page and "Observation steps" in page


def test_report_escapes_imported_text(tmp_path):
    saved = json.loads((ROOT / "docs/ledger-fixture.json").read_bytes())
    changed = deepcopy(saved)
    changed["rows"][0]["status"] = "<script>bad()</script>"
    path = tmp_path / "view.json"
    path.write_text(json.dumps(changed))
    page = render_outputs(path)["ledger.html"].decode()
    assert "<script>bad()" not in page
    assert "&lt;script&gt;bad()&lt;/script&gt;" in page


def test_report_shows_future_horizon_and_availability_without_inventing_publication(
    tmp_path,
):
    saved = json.loads((ROOT / "docs/ledger-fixture.json").read_bytes())
    changed = deepcopy(saved)
    row = changed["rows"][0]
    row["issuance"].update(
        evidence_kind="real_issuance",
        classification="forecast",
        horizon=3,
        target_month="2026-11",
    )
    row["issuance"]["vintage"]["available_through"] = "2026-08"
    row["issuance"]["source"].update(
        published_at=None,
        publication_verified=False,
        availability_receipt={"kind": "available_by_capture"},
    )
    row.update(status="unobserved", outcome=None)
    path = tmp_path / "render-fixture.json"
    raw = json.dumps(changed).encode()
    path.write_bytes(raw)
    output = render_outputs(path)
    page = output["ledger.html"].decode()
    assert "<td>2026-11</td><td>2026-08</td><td>3</td><td>forecast</td>" in page
    assert "Observed availability only" in page
    assert "not their exact first publication time" in page
    assert output["ledger.json"] == raw
