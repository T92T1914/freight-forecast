"""Check causal predictions and independently reconcile the reported errors."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

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
