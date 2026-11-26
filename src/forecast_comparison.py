"""Test whether the differences in the real time reports can be told from noise.

The protocol is docs/forecast-comparison-protocol.json. It was merged before any
statistic here was computed. Nothing is tuned, no model is selected and no
published number changes.
"""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from src.real_time_backtest import PROTOCOL as REAL_TIME_PROTOCOL
from src.real_time_backtest import evaluate
from src.vintages import load_vintages

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/forecast-comparison-protocol.json"
SINGLE = ROOT / "docs/real-time-example.json"
COMPONENTS = ROOT / "docs/real-time-components.json"
# The saved replays call the refit Ridge "model".
KEYS = {
    "monthly_refit_ridge": "model",
    "last_observation": "last_observation",
    "seasonal_naive": "seasonal_naive",
}
TESTS = ("diebold_mariano_hln", "fixed_smoothing")
FLAT = 1e-12
REPRODUCE = 1e-6


def loss_table(rows: list[dict], outcome: str, loss: str) -> pd.DataFrame:
    """One loss column per method, one row per target month."""
    actual = np.array([row[outcome] for row in rows], dtype=float)
    power = {"absolute": 1, "squared": 2}[loss]
    return pd.DataFrame(
        {
            method: np.abs(np.array([row[key] for row in rows]) - actual) ** power
            for method, key in KEYS.items()
        }
    )


def _flat(differential: np.ndarray) -> bool:
    return bool(np.ptp(differential) <= FLAT * np.abs(differential).max())


def _result(statistic: float, freedom: int) -> dict:
    return {
        "statistic": float(statistic),
        "p_value": float(2 * stats.t.sf(abs(statistic), freedom)),
    }


def diebold_mariano_hln(differential: np.ndarray) -> dict | None:
    """Diebold Mariano at horizon 1 with the Harvey, Leybourne, Newbold factor."""
    d = np.asarray(differential, dtype=float)
    months = len(d)
    variance = float(np.mean((d - d.mean()) ** 2))
    if _flat(d) or variance == 0:
        return None
    correction = np.sqrt((months - 1) / months)
    return _result(d.mean() / np.sqrt(variance / months) * correction, months - 1)


def fixed_smoothing(differential: np.ndarray, m: int) -> dict | None:
    """Equal weights on the first m periodogram ordinates, t with 2m degrees."""
    d = np.asarray(differential, dtype=float)
    months = len(d)
    time = np.arange(1, months + 1)
    ordinates = [
        abs(np.sum(d * np.exp(-2j * np.pi * j * time / months))) ** 2 / months
        for j in range(1, m + 1)
    ]
    variance = float(np.mean(ordinates))
    if _flat(d) or variance == 0:
        return None
    return _result(np.sqrt(months) * d.mean() / np.sqrt(variance), 2 * m)


def holm(p_values: list[float]) -> list[float]:
    """Holm step down adjusted p values, in the order given."""
    adjusted = [0.0] * len(p_values)
    running = 0.0
    order = sorted(range(len(p_values)), key=p_values.__getitem__)
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def verdict(p_values: list[float | None], level: float) -> str:
    """Read the two tests of one pair by the protocol's rule."""
    computed = [p for p in p_values if p is not None]
    if not computed:
        return "not_computed"
    below = sum(p < level for p in computed)
    if below == 2:
        return "told_apart"
    return "unsettled" if below == 1 else "not_told_apart"


def confidence_set(losses: pd.DataFrame, settings: dict, block: int) -> dict | None:
    """The model confidence set of arch, or None where two methods never differ."""
    # arch is a dev dependency and stays out of the serving image.
    from arch.bootstrap import MCS

    values = losses.to_numpy()
    for first in range(values.shape[1]):
        for second in range(first + 1, values.shape[1]):
            gap = np.abs(values[:, first] - values[:, second]).max()
            if gap <= FLAT * values.max():
                return None
    result = MCS(
        losses,
        size=settings["size"],
        reps=settings["replications"],
        block_size=block,
        method=settings["method"],
        bootstrap=settings["bootstrap"],
        seed=settings["seed"],
    )
    result.compute()
    included = set(result.included)
    return {
        "set": [name for name in losses.columns if name in included],
        "p_values": {
            name: float(result.pvalues.loc[name, "Pvalue"]) for name in losses.columns
        },
    }


def compare_series(rows: list[dict], protocol: dict) -> dict:
    """Every pair test and confidence set for one series, before any adjustment."""
    settings = protocol["model_confidence_set"]
    m = protocol["tests"]["fixed_smoothing"]["bandwidth_m"]
    result = {}
    for outcome in protocol["data"]["outcomes"]:
        result[outcome] = {}
        for loss in protocol["losses"]:
            losses = loss_table(rows, outcome, loss)
            pairs = []
            for first, second in protocol["pairs"]:
                d = (losses[first] - losses[second]).to_numpy()
                pairs.append(
                    {
                        "first": first,
                        "second": second,
                        "mean_difference": float(d.mean()),
                        "diebold_mariano_hln": diebold_mariano_hln(d),
                        "fixed_smoothing": fixed_smoothing(d, m),
                    }
                )
            blocks = (settings["block_length"], settings["sensitivity_block_length"])
            result[outcome][loss] = {
                "pairs": pairs,
                "confidence_sets": {
                    str(block): confidence_set(losses, settings, block)
                    for block in blocks
                },
            }
    return result


def adjust_and_read(results: dict, protocol: dict) -> None:
    """Add Holm adjusted p values across the component series, then each verdict."""
    components = protocol["data"]["component_series"]
    level = protocol["significance_level"]
    for outcome in protocol["data"]["outcomes"]:
        for loss in protocol["losses"]:
            for position in range(len(protocol["pairs"])):
                for test in TESTS:
                    members = [
                        results[s][outcome][loss]["pairs"][position][test]
                        for s in components
                    ]
                    adjusted = holm(
                        [1.0 if r is None else r["p_value"] for r in members]
                    )
                    for member, value in zip(members, adjusted, strict=True):
                        if member is not None:
                            member["p_adjusted"] = value
                for series, by_outcome in results.items():
                    pair = by_outcome[outcome][loss]["pairs"][position]
                    key = "p_adjusted" if series in components else "p_value"
                    pair["verdict"] = verdict(
                        [None if pair[t] is None else pair[t][key] for t in TESTS],
                        level,
                    )


def _same_vintage_targets(rows: list[dict]) -> int:
    return sum(row["origin_vintage"] == row["first_release_vintage"] for row in rows)


def _require_reproduced(series: str, rows: list[dict], saved: dict) -> None:
    """Stop unless the inputs give the saved mean absolute errors."""
    for outcome, expected in saved.items():
        means = loss_table(rows, outcome, "absolute").mean()
        for method, key in KEYS.items():
            if not np.isclose(means[method], expected[key], rtol=REPRODUCE, atol=0):
                raise ValueError(
                    f"{series} {outcome} {method} does not reproduce the saved error"
                )


def load_single() -> list[dict]:
    """The retained TSIFRGHT predictions, checked against their own report."""
    saved = json.loads(SINGLE.read_bytes())
    rows = saved["predictions"]
    expected = {
        outcome: {key: result["overall"][key]["mae"] for key in KEYS.values()}
        for outcome, result in saved["results"].items()
    }
    _require_reproduced("TSIFRGHT", rows, expected)
    return rows


def load_component(series: str, vintage_dir: Path) -> tuple[list[dict], str]:
    """Replay one component series and check it against the saved panel."""
    stem = series.lower() + "-alfred-vintages"
    manifest_path = ROOT / "data/public" / f"{stem}.json"
    table = load_vintages(vintage_dir / f"{stem}.csv", manifest_path)
    rows = evaluate(table, json.loads(REAL_TIME_PROTOCOL.read_bytes()))["predictions"]
    saved = json.loads(COMPONENTS.read_bytes())["series"][series]
    names = {
        "model": "model_mae",
        "last_observation": "last_observation_mae",
        "seasonal_naive": "seasonal_naive_mae",
    }
    expected = {
        outcome: {key: saved[outcome][name] for key, name in names.items()}
        for outcome in ("first_release", "latest_available")
    }
    _require_reproduced(series, rows, expected)
    return rows, json.loads(manifest_path.read_bytes())["table_sha256"]


def run(vintage_dir: Path) -> dict:
    """Check every input first, then compute every statistic."""
    protocol_raw = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_raw)
    single = protocol["data"]["series"]
    rows = {single: load_single()}
    inputs = {single: {"source": "docs/real-time-example.json"}}
    for series in protocol["data"]["component_series"]:
        rows[series], digest = load_component(series, vintage_dir)
        inputs[series] = {"table_sha256": digest}
    for series, series_rows in rows.items():
        inputs[series]["months"] = len(series_rows)
        inputs[series]["same_vintage_targets"] = _same_vintage_targets(series_rows)
    results = {series: compare_series(r, protocol) for series, r in rows.items()}
    adjust_and_read(results, protocol)
    return {
        "schema_version": 1,
        "protocol_id": protocol["id"],
        "protocol_sha256": hashlib.sha256(
            protocol_raw.replace(b"\r\n", b"\n")
        ).hexdigest(),
        "protocol_hash_line_endings": "LF",
        "packages": {name: version(name) for name in ("numpy", "scipy", "arch")},
        "inputs": inputs,
        "results": results,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vintage-dir",
        type=Path,
        required=True,
        help="directory holding all seven component vintage tables",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.vintage_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Saved forecast comparison to {args.output}")


if __name__ == "__main__":
    main()
