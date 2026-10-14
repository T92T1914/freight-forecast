"""Check causal predictions and independently reconcile the reported errors."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import backtest as backtest_mod
from src.backtest import backtest, main
from src.generate_data import generate
from src.train import train


@pytest.fixture(scope="module")
def report():
    return backtest(generate())


def test_every_origin_predicts_only_the_next_month(report):
    rows = report["predictions"]
    assert len(rows) == 24
    assert rows[0]["month"] == "2023-01"
    assert rows[-1]["month"] == "2024-12"
    assert [row["training_months"] for row in rows] == list(range(60, 84))
    for row in rows:
        assert pd.Period(row["month"], "M") == (
            pd.Period(row["trained_through"], "M") + 1
        )


def test_first_fit_matches_existing_training_model(report):
    # Same observations and estimator as the existing fixed-split experiment.
    # Later predictions may differ because the backtest refits each month.
    from src.features import build_features

    data = generate()
    artifact = train(data)
    features, columns = build_features(data)
    prediction = artifact["model"].predict(features[columns].iloc[[-24]])[0]
    assert report["predictions"][0]["predicted_volume"] == pytest.approx(prediction)


def test_changing_current_and_future_targets_cannot_change_earlier_predictions():
    data = generate().iloc[:30].copy()
    original = backtest(data, initial_train_months=12)
    changed = data.copy()
    changed.loc[26:, "volume"] *= 3
    altered = backtest(changed, initial_train_months=12)
    for before, after in zip(
        original["predictions"][:3], altered["predictions"][:3], strict=True
    ):
        assert before["predicted_volume"] == after["predicted_volume"]
        assert (
            before["naive_same_month_last_year"] == after["naive_same_month_last_year"]
        )
    # The altered target affects its own error but not its prediction. Once
    # observed, it CAN affect the next month's fit and rolling-history feature.
    assert original["predictions"][2]["actual"] != altered["predictions"][2]["actual"]
    assert (
        original["predictions"][3]["predicted_volume"]
        != altered["predictions"][3]["predicted_volume"]
    )


def test_report_metrics_reconcile_with_actual_observations(report):
    rows = report["predictions"]
    expected = generate().iloc[-24:]
    np.testing.assert_array_equal([row["actual"] for row in rows], expected["volume"])
    np.testing.assert_array_equal(
        [row["naive_same_month_last_year"] for row in rows],
        generate()["volume"].iloc[-36:-12],
    )
    groups = [(report["overall"], rows)]
    groups.extend(
        (summary, [row for row in rows if row["season"] == season])
        for season, summary in report["by_season"].items()
    )
    groups.extend(
        (summary, [row for row in rows if row["month"][:4] == year])
        for year, summary in report["by_year"].items()
    )
    for summary, subset in groups:
        actual = np.array([row["actual"] for row in subset])
        model = np.array([row["predicted_volume"] for row in subset])
        naive = np.array([row["naive_same_month_last_year"] for row in subset])
        model_error, naive_error = abs(actual - model), abs(actual - naive)
        assert summary["months"] == len(subset)
        assert summary["model_mae"] == pytest.approx(model_error.mean())
        assert summary["naive_mae"] == pytest.approx(naive_error.mean())
        assert summary["model_mape"] == pytest.approx((model_error / actual).mean())
        assert summary["naive_mape"] == pytest.approx((naive_error / actual).mean())
        assert summary["mae_delta_model_minus_naive"] == pytest.approx(
            model_error.mean() - naive_error.mean()
        )
        assert summary["mae_improvement_fraction"] == pytest.approx(
            1 - model_error.mean() / naive_error.mean()
        )
        assert summary["model_better_months"] == int((model_error < naive_error).sum())
        assert summary["model_worse_months"] == int((model_error > naive_error).sum())
        assert summary["tied_months"] == int((model_error == naive_error).sum())
    assert report["by_season"]["peak"]["months"] == 8
    assert report["by_season"]["off_peak"]["months"] == 16


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf, -np.inf, "bad"])
@pytest.mark.parametrize("index", [0, 72, 95])
def test_invalid_targets_and_training_observations_are_rejected(value, index):
    data = generate().astype({"volume": object})
    data.loc[index, "volume"] = value
    with pytest.raises(ValueError, match="finite, positive"):
        backtest(data)


@pytest.mark.parametrize("problem", ["gap", "duplicate", "not_month_start", "missing"])
def test_invalid_calendar_is_rejected(problem):
    data = generate()
    if problem == "gap":
        data = data.drop(index=80)
    elif problem == "duplicate":
        data.loc[80, "date"] = data.loc[79, "date"]
    elif problem == "missing":
        data.loc[80, "date"] = pd.NaT
    else:
        data.loc[80, "date"] += pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="contiguous monthly"):
        backtest(data)


@pytest.mark.parametrize("initial", [0, 11, 12.5, True, "12"])
def test_invalid_training_window(initial):
    with pytest.raises(ValueError, match="integer of at least 12"):
        backtest(generate(), initial_train_months=initial)


def test_training_window_must_leave_an_evaluation_month():
    with pytest.raises(ValueError, match="need at least 73"):
        backtest(generate().iloc[:72])


def test_empty_data_and_missing_columns():
    with pytest.raises(ValueError, match="observed months"):
        backtest(generate().iloc[:0])
    with pytest.raises(ValueError, match="date and volume"):
        backtest(generate().drop(columns="volume"))


def test_order_independence_and_no_mutation(report):
    data = generate().sample(frac=1, random_state=123)
    before = data.copy(deep=True)
    assert backtest(data) == report
    pd.testing.assert_frame_equal(data, before)


def test_perfect_baseline_and_empty_season_produce_valid_json():
    data = generate().iloc[:25].copy()
    data["volume"] = 1000.0
    report = backtest(data, initial_train_months=12)
    assert report["overall"]["naive_mae"] == 0
    assert report["overall"]["mae_improvement_fraction"] is None
    assert report["by_season"]["peak"] == {"months": 0}
    json.dumps(report, allow_nan=False)


def test_cli_records_exact_input_and_does_not_write_a_model(tmp_path, monkeypatch):
    from src import train

    def no_artifact(*args, **kwargs):
        pytest.fail("backtesting must not write a model artifact")

    monkeypatch.setattr(train.joblib, "dump", no_artifact)
    path = tmp_path / "observations.csv"
    generate().iloc[:26].to_csv(path, index=False)
    output = tmp_path / "reports" / "result.json"
    args = [
        "--data",
        str(path),
        "--initial-train-months",
        "12",
        "--output",
        str(output),
    ]
    main(args)
    first = output.read_bytes()
    result = json.loads(first)
    assert result["overall"]["months"] == 2
    assert (
        result["provenance"]["data_sha256"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert result["provenance"]["packages"]["scikit-learn"]
    assert len(result["provenance"]["implementation_sha256"]) == 3
    main(args)
    assert output.read_bytes() == first


def test_cli_rejects_input_overwrite_and_missing_input(tmp_path):
    path = tmp_path / "observations.csv"
    path.write_text("preserve me")
    with pytest.raises(SystemExit) as exc:
        main(["--data", str(path), "--output", str(path)])
    assert exc.value.code == 2
    assert path.read_text() == "preserve me"
    with pytest.raises(SystemExit) as exc:
        main(["--data", str(tmp_path / "missing.csv")])
    assert exc.value.code == 2


def test_cli_stdout_is_json(tmp_path, capsys):
    path = tmp_path / "observations.csv"
    generate().iloc[:25].to_csv(path, index=False)
    main(["--data", str(path), "--initial-train-months", "12"])
    result = json.loads(capsys.readouterr().out)
    assert result["overall"]["months"] == 1


def _link(source, destination, *, symbolic=False):
    try:
        if symbolic:
            destination.symlink_to(source)
        else:
            os.link(source, destination)
    except (NotImplementedError, OSError) as error:
        kind = "symlink" if symbolic else "hard-link"
        pytest.skip(f"{kind} creation unavailable on this platform: {error}")


@pytest.mark.parametrize("kind", ["separate", "same", "symlink", "hardlink"])
def test_actual_cli_preserves_input_and_rejects_aliases(tmp_path, kind):
    source = tmp_path / "observations.csv"
    generate().iloc[:25].to_csv(source, index=False)
    original = source.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    destination = source if kind == "same" else tmp_path / "report.json"
    if kind in {"symlink", "hardlink"}:
        _link(source, destination, symbolic=kind == "symlink")
        assert destination.samefile(source)
    elif kind == "separate":
        destination.write_bytes(b"previous completed output")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.backtest",
            "--data",
            os.fspath(source),
            "--initial-train-months",
            "12",
            "--output",
            os.fspath(destination),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert source.read_bytes() == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    if kind == "separate":
        assert result.returncode == 0, result.stderr
        report = json.loads(destination.read_bytes())
        assert report["overall"]["months"] == 1
        assert report["provenance"]["data_sha256"] == original_hash
        assert "Saved 1 monthly predictions" in result.stdout
    else:
        assert result.returncode == 2
        assert "output must not overwrite the input dataset" in result.stderr
        assert result.stdout == ""
        assert destination.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source, destination}


def test_cli_keeps_symlink_to_separate_output_target(tmp_path):
    source = tmp_path / "observations.csv"
    generate().iloc[:25].to_csv(source, index=False)
    original = source.read_bytes()
    target = tmp_path / "report.json"
    target.write_bytes(b"previous complete output")
    destination = tmp_path / "report-link.json"
    _link(target, destination, symbolic=True)

    main(
        [
            "--data",
            str(source),
            "--initial-train-months",
            "12",
            "--output",
            str(destination),
        ]
    )

    assert destination.is_symlink()
    assert json.loads(target.read_bytes())["overall"]["months"] == 1
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source, target, destination}


@pytest.mark.parametrize(
    "failure",
    ["serialization", "create", "write", "flush", "fsync", "close", "replace"],
)
def test_cli_delivery_failure_preserves_input_and_old_output(
    tmp_path, monkeypatch, capsys, failure
):
    source = tmp_path / "observations.csv"
    generate().iloc[:25].to_csv(source, index=False)
    original = source.read_bytes()
    destination = tmp_path / "report.json"
    destination.write_bytes(b"previous complete output")
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"not owned by backtest")

    def fail(*args, **kwargs):
        raise OSError(f"injected {failure} failure")

    if failure == "serialization":
        monkeypatch.setattr(
            backtest_mod,
            "backtest",
            lambda *args, **kwargs: {"overall": {"months": 1}, "bad": float("nan")},
        )
    elif failure == "create":
        monkeypatch.setattr(backtest_mod.tempfile, "NamedTemporaryFile", fail)
    elif failure in {"write", "flush", "close"}:
        create = backtest_mod.tempfile.NamedTemporaryFile

        class FailingStream:
            def __init__(self, *args, **kwargs):
                self.stream = create(*args, **kwargs)
                self.name = self.stream.name

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.__exit__(*args)
                if failure == "close":
                    fail()

            def write(self, value):
                self.stream.write(value[:10] if failure == "write" else value)
                if failure == "write":
                    fail()

            def flush(self):
                if failure == "flush":
                    fail()
                self.stream.flush()

            def fileno(self):
                return self.stream.fileno()

        monkeypatch.setattr(backtest_mod.tempfile, "NamedTemporaryFile", FailingStream)
    elif failure == "fsync":
        monkeypatch.setattr(backtest_mod.os, "fsync", fail)
    else:
        monkeypatch.setattr(backtest_mod.os, "replace", fail)

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "--data",
                str(source),
                "--initial-train-months",
                "12",
                "--output",
                str(destination),
            ]
        )

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert "error:" in output.err
    assert output.out == ""
    assert source.read_bytes() == original
    assert destination.read_bytes() == b"previous complete output"
    assert unrelated.read_bytes() == b"not owned by backtest"
    assert set(tmp_path.iterdir()) == {source, destination, unrelated}


def test_delivery_rechecks_alias_after_staging(tmp_path, monkeypatch):
    source = tmp_path / "observations.csv"
    source.write_bytes(b"retained input")
    destination = tmp_path / "report.json"
    destination.write_bytes(b"previous output")
    probe = tmp_path / "link-probe"
    _link(source, probe)
    probe.unlink()
    fsync = backtest_mod.os.fsync

    def change_destination(descriptor):
        fsync(descriptor)
        destination.unlink()
        os.link(source, destination)

    monkeypatch.setattr(backtest_mod.os, "fsync", change_destination)
    with pytest.raises(ValueError, match="must not overwrite the input dataset"):
        backtest_mod._write_report("completed report", destination, source)

    assert destination.samefile(source)
    assert source.read_bytes() == destination.read_bytes() == b"retained input"
    assert set(tmp_path.iterdir()) == {source, destination}


@pytest.mark.parametrize("primary", [OSError, KeyboardInterrupt, SystemExit])
def test_delivery_cleanup_failure_preserves_original_error(
    tmp_path, monkeypatch, primary
):
    source = tmp_path / "observations.csv"
    source.write_bytes(b"retained input")
    destination = tmp_path / "report.json"
    destination.write_bytes(b"previous complete output")
    failure = primary("original replacement failure")

    def fail_replace(*args):
        raise failure

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("injected cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(backtest_mod.os, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(primary) as captured:
            backtest_mod._write_report("completed report", destination, source)

    assert captured.value is failure
    assert any("temporary report cleanup failed" in note for note in failure.__notes__)
    assert source.read_bytes() == b"retained input"
    assert destination.read_bytes() == b"previous complete output"
    retained = set(tmp_path.iterdir()) - {source, destination}
    assert len(retained) == 1
    temporary = retained.pop()
    assert temporary.read_text(encoding="utf-8") == "completed report"
    temporary.unlink()


def test_cli_reports_cleanup_failure_without_masking_delivery_error(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "observations.csv"
    generate().iloc[:25].to_csv(source, index=False)
    original = source.read_bytes()
    destination = tmp_path / "report.json"
    destination.write_bytes(b"previous complete output")

    def fail_replace(*args):
        raise OSError("original replacement failure")

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("injected cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(backtest_mod.os, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(SystemExit) as captured:
            main(
                [
                    "--data",
                    str(source),
                    "--initial-train-months",
                    "12",
                    "--output",
                    str(destination),
                ]
            )

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert "original replacement failure" in output.err
    assert "temporary report cleanup failed: PermissionError" in output.err
    assert output.out == ""
    assert source.read_bytes() == original
    assert destination.read_bytes() == b"previous complete output"
    temporary = (set(tmp_path.iterdir()) - {source, destination}).pop()
    assert json.loads(temporary.read_bytes())["overall"]["months"] == 1
    temporary.unlink()
