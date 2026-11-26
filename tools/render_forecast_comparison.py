"""Write the tables of docs/forecast-comparison.md from the saved statistics."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "docs/forecast-comparison.json"
DOCUMENT = ROOT / "docs/forecast-comparison.md"
START = "<!-- tables start -->"
END = "<!-- tables end -->"
SERIES = {
    "TSIFRGHT": "Freight index",
    "TRUCKD11": "Truck tonnage",
    "RAILFRTCARLOADSD11": "Rail carloads",
    "RAILFRTINTERMODALD11": "Rail intermodal",
    "WATERBORNED11": "Inland waterway",
    "AIRRTMFMD11": "Air freight",
    "PETROLEUMD11": "Pipeline petroleum",
    "NATURALGASD11": "Natural gas",
}
METHODS = {
    "monthly_refit_ridge": "Ridge",
    "last_observation": "Last",
    "seasonal_naive": "Last year",
}
OUTCOMES = {"first_release": "first release", "latest_available": "latest values"}
LOSSES = {"absolute": "absolute loss", "squared": "squared loss"}
PAIRS = (
    ("monthly_refit_ridge", "last_observation"),
    ("monthly_refit_ridge", "seasonal_naive"),
    ("last_observation", "seasonal_naive"),
)
VERDICTS = {
    "told_apart": "Told apart",
    "unsettled": "Unsettled",
    "not_told_apart": "Not told apart",
    "not_computed": "Not computed",
}


def _p(value: float) -> str:
    return "<0.0001" if value < 0.0001 else f"{value:.4f}"


def _table(header: list[str], rows: list[list[str]], left: int) -> list[str]:
    align = ["---"] * left + ["---:"] * (len(header) - left)
    return ["| " + " | ".join(r) + " |" for r in [header, align, *rows]]


def _pair_name(first: str, second: str) -> str:
    return f"{METHODS[first]} minus {METHODS[second].lower()}"


def _test_cells(result: dict | None) -> list[str]:
    if result is None:
        return ["none", "none", "none"]
    adjusted = result.get("p_adjusted")
    return [
        f"{result['statistic']:.3f}",
        _p(result["p_value"]),
        "none" if adjusted is None else _p(adjusted),
    ]


def counts_table(results: dict) -> list[str]:
    rows = []
    for position, pair in enumerate(PAIRS):
        for outcome, outcome_name in OUTCOMES.items():
            for loss, loss_name in LOSSES.items():
                found = [
                    by[outcome][loss]["pairs"][position]["verdict"]
                    for by in results.values()
                ]
                rows.append(
                    [_pair_name(*pair), outcome_name, loss_name]
                    + [str(found.count(v)) for v in VERDICTS]
                )
    return _table(["Pair", "Outcome", "Loss", *VERDICTS.values()], rows, 3)


def pair_table(results: dict, outcome: str, loss: str) -> list[str]:
    rows = []
    for series, by in results.items():
        for pair in by[outcome][loss]["pairs"]:
            rows.append(
                [
                    SERIES[series],
                    _pair_name(pair["first"], pair["second"]),
                    f"{pair['mean_difference']:.4g}",
                    *_test_cells(pair["diebold_mariano_hln"]),
                    *_test_cells(pair["fixed_smoothing"]),
                    VERDICTS[pair["verdict"]],
                ]
            )
    header = [
        "Series",
        "Pair",
        "Mean difference",
        "DM statistic",
        "DM p",
        "DM Holm p",
        "Fixed statistic",
        "Fixed p",
        "Fixed Holm p",
        "Reading",
    ]
    return _table(header, rows, 2)


def _set_cell(found: dict | None) -> str:
    if found is None:
        return "not computed"
    return ", ".join(METHODS[name] for name in found["set"])


def set_table(results: dict) -> list[str]:
    rows = []
    for series, by in results.items():
        for outcome, outcome_name in OUTCOMES.items():
            for loss, loss_name in LOSSES.items():
                sets = by[outcome][loss]["confidence_sets"]
                main_set = sets["4"]
                p_values = [
                    "none" if main_set is None else _p(main_set["p_values"][name])
                    for name in METHODS
                ]
                rows.append(
                    [
                        SERIES[series],
                        outcome_name,
                        loss_name,
                        _set_cell(main_set),
                        *p_values,
                        _set_cell(sets["12"]),
                    ]
                )
    header = [
        "Series",
        "Outcome",
        "Loss",
        "Set at block 4",
        "Ridge p",
        "Last p",
        "Last year p",
        "Set at block 12",
    ]
    return _table(header, rows, 4)


def vintage_table(inputs: dict) -> list[str]:
    rows = [
        [SERIES[s], str(v["same_vintage_targets"]), str(v["months"])]
        for s, v in inputs.items()
    ]
    header = ["Series", "Targets already on the origin vintage", "Targets"]
    return _table(header, rows, 1)


def render(report: dict) -> str:
    results = report["results"]
    lines = ["### Readings across the eight series", ""]
    lines += counts_table(results)
    for outcome, outcome_name in OUTCOMES.items():
        for loss, loss_name in LOSSES.items():
            lines += ["", f"### Pairs, {outcome_name}, {loss_name}", ""]
            lines += pair_table(results, outcome, loss)
    lines += ["", "### Model confidence sets", ""]
    lines += set_table(results)
    lines += ["", "### Origins that are not one step ahead in publication time", ""]
    lines += vintage_table(report["inputs"])
    return "\n".join(lines)


def place(document: str, tables: str) -> str:
    before, rest = document.split(START)
    _, after = rest.split(END)
    return f"{before}{START}\n\n{tables}\n\n{END}{after}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved", type=Path, default=SAVED)
    parser.add_argument("--document", type=Path, default=DOCUMENT)
    args = parser.parse_args(argv)
    report = json.loads(args.saved.read_text(encoding="utf-8"))
    text = args.document.read_text(encoding="utf-8")
    args.document.write_text(
        place(text, render(report)), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
