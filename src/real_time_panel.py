"""Run the real time protocol on the component series of the freight index.

Each series gets the same three forecasters, the same 72 origins and the same
outcome policies. Nothing is tuned. The scaled error is the model's mean absolute
error divided by a naive baseline's on the same origins, so a value above 1 means
the model lost to that baseline.
"""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median

from src.real_time_backtest import PROTOCOL, evaluate
from src.vintages import load_vintages

OUTCOMES = ("first_release", "latest_available")


def scaled(result: dict) -> dict:
    overall = result["overall"]
    model = overall["model"]["mae"]
    return {
        "model_mae": model,
        "last_observation_mae": overall["last_observation"]["mae"],
        "seasonal_naive_mae": overall["seasonal_naive"]["mae"],
        "scaled_vs_last_observation": model / overall["last_observation"]["mae"],
        "scaled_vs_seasonal_naive": model / overall["seasonal_naive"]["mae"],
    }


def summarize(per_series: dict) -> dict:
    out = {}
    for outcome in OUTCOMES:
        out[outcome] = {}
        for key in ("scaled_vs_last_observation", "scaled_vs_seasonal_naive"):
            values = [s[outcome][key] for s in per_series.values()]
            out[outcome][key] = {
                "mean": mean(values),
                "median": median(values),
                "series_where_model_wins": sum(v < 1 for v in values),
                "series": len(values),
            }
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vintage-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    protocol_raw = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_raw)
    per_series = {}
    for series in protocol["component_series"]["ids"]:
        stem = series.lower() + "-alfred-vintages"
        table = load_vintages(
            args.vintage_dir / f"{stem}.csv", args.vintage_dir / f"{stem}.json"
        )
        report = evaluate(table, protocol)
        manifest = json.loads((args.vintage_dir / f"{stem}.json").read_bytes())
        per_series[series] = {
            "months": report["months"],
            "first_vintage": manifest["first_vintage"],
            "latest_vintage": report["latest_vintage"],
            "table_sha256": manifest["table_sha256"],
            "retrieved_utc": manifest["retrieved_utc"],
            **{o: scaled(report["results"][o]) for o in OUTCOMES},
        }
    result = {
        "schema_version": 1,
        "evaluation_kind": "real_time_component_series_one_observation_step",
        "protocol_id": protocol["id"],
        "protocol_sha256": hashlib.sha256(
            protocol_raw.replace(b"\r\n", b"\n")
        ).hexdigest(),
        "series": per_series,
        "across_series": summarize(per_series),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"Saved component evaluation to {args.output}")


if __name__ == "__main__":
    main()
