"""Protect the declared development/test boundary and independent baselines."""

import copy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from src import real_backtest as rb
from src.generate_data import generate


@pytest.fixture
def protocol():
    return {
        "schema_version": 1,
        "data_start": "2017-01",
        "training_end": "2018-12",
        "development_start": "2019-01",
        "development_end": "2019-02",
        "test_start": "2019-03",
        "test_end": "2019-05",
        "model": "StandardScaler + Ridge(alpha=1.0), log target / exp inverse",
        "policies": ["fixed", "monthly_refit"],
        "selection": {
            "metric": "development_mae",
            "tie_break": "fixed",
            "final_candidates": "selected_policy_only",
        },
        "baselines": ["last_observation", "seasonal_naive"],
    }


def test_future_test_values_cannot_select_policy_or_change_development(protocol):
    original = generate().iloc[:29].copy()
    changed = original.copy()
    changed.loc[26:, "volume"] *= 10
    before, after = rb.evaluate(original, protocol), rb.evaluate(changed, protocol)
    assert before["development"] == after["development"]
    assert before["selected_policy"] == after["selected_policy"]
    first_before, first_after = (
        before["test"]["predictions"][0],
        after["test"]["predictions"][0],
    )
    for method in rb.METHODS:
        assert first_before[method] == first_after[method]
    assert first_before["actual"] != first_after["actual"]


def test_excluded_later_observations_change_nothing(protocol):
    original = generate()
    changed = original.copy()
    changed.loc[29:, "volume"] *= 100
    assert rb.evaluate(original, protocol) == rb.evaluate(changed, protocol)


def test_development_only_never_evaluates_test(protocol, monkeypatch):
    original = rb._block
    calls = []

    def capture(history, start, end, policy):
        calls.append((start, end))
        assert history["date"].max() <= pd.Timestamp("2019-02-01")
        return original(history, start, end, policy)

    monkeypatch.setattr(rb, "_block", capture)
    result = rb.evaluate(generate().iloc[:26], protocol, development_only=True)
    assert result["test"] is None
    assert len(calls) == 2


def test_selected_policy_uses_development_mae_and_exact_fixed_tie(
    protocol, monkeypatch
):
    for errors, expected in [
        ((1.0, 2.0), "fixed"),
        ((2.0, 1.0), "monthly_refit"),
        ((1.0, 1.0), "fixed"),
    ]:
        calls = []

        def fake_block(history, start, end, policy, _calls=calls, _errors=errors):
            _calls.append((str(start), policy))
            return {"metrics": {"model": {"mae": _errors[rb.POLICIES.index(policy)]}}}

        monkeypatch.setattr(rb, "_block", fake_block)
        result = rb.evaluate(generate(), protocol)
        assert result["selected_policy"] == expected
        assert calls == [
            ("2019-01", "fixed"),
            ("2019-01", "monthly_refit"),
            ("2019-03", expected),
        ]


def test_metrics_baselines_and_final_fit_are_independently_reconciled(protocol):
    history = generate()
    result = rb.evaluate(history, protocol)
    for block in [*result["development"].values(), result["test"]]:
        for row in block["predictions"]:
            index = history.index[history["date"] == pd.Timestamp(row["month"])][0]
            assert row["actual"] == history.loc[index, "volume"]
            assert row["last_observation"] == history.loc[index - 1, "volume"]
            assert row["seasonal_naive"] == history.loc[index - 12, "volume"]
            assert row["observed_through"] == str(pd.Period(row["month"], "M") - 1)
            assert row["trained_through"] < row["month"]
            for method in rb.METHODS:
                assert row["absolute_errors"][method] == abs(
                    row[method] - row["actual"]
                )
        for name in rb.METHODS:
            errors = np.array(
                [row[name] - row["actual"] for row in block["predictions"]]
            )
            assert block["metrics"][name]["mae"] == pytest.approx(np.abs(errors).mean())
            assert block["metrics"][name]["rmse"] == pytest.approx(
                np.sqrt((errors**2).mean())
            )
            actual = np.array([row["actual"] for row in block["predictions"]])
            assert block["metrics"][name]["mape"] == pytest.approx(
                (np.abs(errors) / actual).mean()
            )
    assert result["test"]["predictions"][0]["trained_through"] == "2019-02"


def test_fixed_coefficients_keep_cutoff_but_lags_use_observed_history(protocol):
    result = rb.evaluate(generate(), protocol)
    fixed = result["development"]["fixed"]["predictions"]
    refit = result["development"]["monthly_refit"]["predictions"]
    assert [row["trained_through"] for row in fixed] == ["2018-12", "2018-12"]
    assert [row["trained_through"] for row in refit] == ["2018-12", "2019-01"]
    assert fixed[1]["last_observation"] == fixed[0]["actual"]
    assert fixed[0]["model"] == refit[0]["model"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("training_end", "2017-12"),
        ("development_start", "2018-12"),
        ("test_start", "2019-02"),
        ("test_end", "2019-02"),
        ("test_end", "2019-13"),
        ("test_end", "2019-5"),
        ("model", "another model"),
        ("baselines", ["seasonal_naive"]),
    ],
)
def test_bad_protocol_rejected(protocol, key, value):
    protocol[key] = value
    with pytest.raises(ValueError):
        rb.evaluate(generate(), protocol)


def test_missing_protocol_month_rejected(protocol):
    with pytest.raises(ValueError, match="cover every"):
        rb.evaluate(generate().iloc[:28], protocol)


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "source.csv"
    source.write_bytes(
        b"obs_date,tsi_freight\n2000-01-01T00:00:00.000,100\n2000-02-01T00:00:00.000,101\n"
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": "bw6n-ddqk",
        "field": "tsi_freight",
        "units": "index_points_2000_average_100",
        "seasonally_adjusted": True,
        "vintage_kind": "latest_vintage_snapshot",
        "rows": 2,
        "first_month": "2000-01",
        "last_month": "2000-02",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return source, path, manifest


def test_snapshot_exact_bytes_and_semantics(snapshot):
    source, path, manifest = snapshot
    data, loaded = rb.load_snapshot(source, path)
    assert loaded == manifest
    assert list(data["volume"]) == [100, 101]
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA256"):
        rb.load_snapshot(source, path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("units", "moves"),
        ("seasonally_adjusted", False),
        ("field", "tsi_freight_c"),
        ("vintage_kind", "as_published"),
        ("rows", 3),
        ("last_month", "2000-03"),
    ],
)
def test_manifest_mismatch_rejected(snapshot, key, value):
    source, path, manifest = snapshot
    manifest[key] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        rb.load_snapshot(source, path)


@pytest.mark.parametrize(
    "replacement",
    [
        b"2000-02-01T00:00:00.000,0",
        b"2000-02-01T00:00:00.000,nan",
        b"2000-02-01T00:00:00.000,inf",
        b"2000-03-01T00:00:00.000,101",
        b"2000-01-01T00:00:00.000,101",
        b"2000-02-02T00:00:00.000,101",
    ],
)
def test_snapshot_invalid_observations_rejected_even_with_matching_hash(
    snapshot, replacement
):
    source, path, manifest = snapshot
    source.write_bytes(
        source.read_bytes().replace(b"2000-02-01T00:00:00.000,101", replacement)
    )
    manifest["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        rb.load_snapshot(source, path)


def test_official_snapshot_identity_and_protocol_coverage():
    data, manifest = rb.load_snapshot(rb.DATA, rb.MANIFEST)
    assert len(data) == manifest["rows"] == 319
    protocol = json.loads(rb.PROTOCOL.read_bytes())
    dates = rb.validate_protocol(protocol)
    assert str(dates[-1]) == "2025-12"
    assert len(data.loc[data["date"].between("2020-01-01", "2025-12-01")]) == 72


def test_saved_evidence_matches_source_protocol_and_independent_errors():
    report = json.loads((rb.ROOT / "docs/real-data-example.json").read_bytes())
    source, manifest = rb.load_snapshot(rb.DATA, rb.MANIFEST)
    protocol = json.loads(rb.PROTOCOL.read_bytes())
    assert report["protocol"] == protocol
    assert report["provenance"]["data"] == manifest
    assert (
        report["provenance"]["protocol_sha256"]
        == hashlib.sha256(rb.PROTOCOL.read_bytes()).hexdigest()
    )
    for name, digest in report["provenance"]["implementation_sha256"].items():
        assert (
            hashlib.sha256(
                (rb.ROOT / name).read_text(encoding="utf-8").encode()
            ).hexdigest()
            == digest
        )
    expected_policy = min(
        rb.POLICIES,
        key=lambda policy: report["development"][policy]["metrics"]["model"]["mae"],
    )
    assert report["selected_policy"] == report["test"]["policy"] == expected_policy
    assert [row["month"] for row in report["test"]["predictions"]] == [
        str(month) for month in pd.period_range("2020-01", "2025-12", freq="M")
    ]
    for block in [*report["development"].values(), report["test"]]:
        for row in block["predictions"]:
            index = source.index[source["date"] == pd.Timestamp(row["month"])][0]
            assert row["actual"] == source.loc[index, "volume"]
            assert row["last_observation"] == source.loc[index - 1, "volume"]
            assert row["seasonal_naive"] == source.loc[index - 12, "volume"]
        groups = [(block["metrics"], block["predictions"])]
        groups.extend(
            (
                metrics,
                [row for row in block["predictions"] if row["month"].startswith(year)],
            )
            for year, metrics in block["by_year"].items()
        )
        for metrics, rows in groups:
            actual = np.array([row["actual"] for row in rows])
            for method in rb.METHODS:
                errors = np.array([row[method] for row in rows]) - actual
                assert metrics[method]["mae"] == pytest.approx(np.abs(errors).mean())
                assert metrics[method]["rmse"] == pytest.approx(
                    np.sqrt((errors**2).mean())
                )
                assert metrics[method]["mape"] == pytest.approx(
                    (np.abs(errors) / actual).mean()
                )


def test_cli_no_model_publication_and_input_protection(tmp_path, protocol, monkeypatch):
    from src import train

    def reject(*args, **kwargs):
        pytest.fail("evaluation must not publish a model")

    monkeypatch.setattr(train, "save_artifact", reject)
    monkeypatch.setattr(train.joblib, "dump", reject)
    monkeypatch.setattr(
        rb, "load_snapshot", lambda *args: (generate(), {"fixture": True})
    )
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    output = tmp_path / "report.json"
    rb.main(["--protocol", str(protocol_path), "--output", str(output)])
    report = json.loads(output.read_bytes())
    assert report["test"]["months"] == 3
    assert (
        report["provenance"]["protocol_sha256"]
        == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    )
    before = copy.deepcopy(protocol_path.read_bytes())
    with pytest.raises(SystemExit):
        rb.main(["--protocol", str(protocol_path), "--output", str(protocol_path)])
    assert protocol_path.read_bytes() == before
