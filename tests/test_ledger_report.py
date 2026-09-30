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


def test_report_escapes_imported_text(tmp_path):
    saved = json.loads((ROOT / "docs/ledger-fixture.json").read_bytes())
    changed = deepcopy(saved)
    changed["rows"][0]["status"] = "<script>bad()</script>"
    path = tmp_path / "view.json"
    path.write_text(json.dumps(changed))
    page = render_outputs(path)["ledger.html"].decode()
    assert "<script>bad()" not in page
    assert "&lt;script&gt;bad()&lt;/script&gt;" in page
