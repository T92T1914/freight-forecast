"""Every number in the planner walkthrough appears in the document it quotes."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WALKTHROUGH = (ROOT / "docs/planner-walkthrough.md").read_text(encoding="utf-8")
SOURCES = (
    "README.md",
    "docs/backtesting.md",
    "docs/forecast-comparison.md",
    "docs/interval-methods.md",
    "docs/prospective-ledger.md",
    "docs/real-data-evaluation.md",
    "docs/real-time-evaluation.md",
    "docs/real-time-components.md",
)
LINK_TARGET = re.compile(r"\]\([^)]*\)")
LIST_MARK = re.compile(r"(?m)^\d+\. ")
NUMBER = re.compile(r"\d+(?:[.:,]\d+)*")


def _numbers(text: str) -> set[str]:
    return set(NUMBER.findall(LIST_MARK.sub("", LINK_TARGET.sub("]", text))))


def test_every_number_is_quoted_from_a_linked_report():
    quoted = set()
    for name in SOURCES:
        assert Path(name).name in WALKTHROUGH, name
        quoted |= _numbers((ROOT / name).read_text(encoding="utf-8"))
    assert _numbers(WALKTHROUGH) - quoted == set()


def test_the_comparison_table_rows_match_their_reports():
    for report, label, row in [
        ("real-data-evaluation.md", "One later snapshot", (1.778, 1.260, 2.783)),
        ("real-time-evaluation.md", "scored on first releases", (1.878, 1.551, 3.138)),
        ("real-time-evaluation.md", "scored on latest values", (2.494, 1.850, 2.828)),
    ]:
        cells = " | ".join(f"{value:.3f}" for value in row)
        line = next(x for x in WALKTHROUGH.splitlines() if label in x)
        assert line.endswith(f"| {cells} |"), label
        source = (ROOT / "docs" / report).read_text(encoding="utf-8")
        for value in row:
            assert f"{value:.3f}" in source


def test_how_far_the_ranking_holds_matches_the_saved_statistics():
    saved = json.loads((ROOT / "docs/forecast-comparison.json").read_bytes())
    ridge, last, naive = "monthly_refit_ridge", "last_observation", "seasonal_naive"
    readings = {
        ((row["first"], row["second"]), series, outcome, loss): row
        for series, outcomes in saved["results"].items()
        for outcome, losses in outcomes.items()
        for loss, found in losses.items()
        for row in found["pairs"]
    }
    words = {
        "told_apart": "Told apart",
        "unsettled": "Unsettled",
        "not_told_apart": "Not told apart",
    }

    table = [
        "| Freight index comparison | Scored on | Miss as it is | Miss squared |",
        "|---|---|---|---|",
    ]
    for pair, name in [
        ((ridge, last), "Model against the latest month"),
        ((ridge, naive), "Model against last year"),
        ((last, naive), "Latest month against last year"),
    ]:
        for outcome, label in [
            ("first_release", "First releases"),
            ("latest_available", "Latest values"),
        ]:
            found = [
                readings[pair, "TSIFRGHT", outcome, loss]
                for loss in ("absolute", "squared")
            ]
            cells = " | ".join(words[row["verdict"]] for row in found)
            table.append(f"| {name} | {label} | {cells} |")
            for row in found:
                if row["verdict"] == "told_apart":
                    # The latest month has the lower loss wherever told apart.
                    assert (row["mean_difference"] > 0) == (pair[1] == last)
    assert "\n".join(table) in WALKTHROUGH

    def of(pair):
        return {k[1:]: v for k, v in readings.items() if k[0] == pair}

    def counts(rows):
        verdicts = [row["verdict"] for row in rows.values()]
        return len(verdicts), *(verdicts.count(word) for word in words)

    prose = " ".join(WALKTHROUGH.replace("\n*", "\n").split())
    against_last = of((ridge, last))
    total, apart, unsettled, not_apart = counts(against_last)
    assert (
        f"give {total} readings of the model against the latest month. {apart} are "
        f"told apart, {unsettled} are unsettled and {not_apart} are not told apart."
    ) in prose
    told = {k: v for k, v in against_last.items() if v["verdict"] == "told_apart"}
    latest_wins = {k for k, v in told.items() if v["mean_difference"] > 0}
    model_wins = set(told) - latest_wins
    assert {k[:2] for k in model_wins} == {("NATURALGASD11", "latest_available")}
    assert (
        f"Of the {len(told)}, the latest month has the lower miss in "
        f"{len(latest_wins)}. The model has it in {len(model_wins)}, both on natural "
        "gas consumption against latest values."
    ) in prose

    against_naive = of((ridge, naive))
    total, apart, unsettled, not_apart = counts(against_naive)
    assert (
        f"Of the {total} readings of the model against the same month last year, "
        f"{apart} are told apart, {unsettled} are unsettled and {not_apart} are not "
        f"told apart. All {apart} are on rail intermodal, with the lower miss for "
        "the model."
    ) in prose
    for key, row in against_naive.items():
        if row["verdict"] == "told_apart":
            assert key[0] == "RAILFRTINTERMODALD11" and row["mean_difference"] < 0

    for loss in ("absolute", "squared"):
        found = saved["results"]["TSIFRGHT"]["first_release"][loss]
        chosen = found["confidence_sets"]["4"]
        assert chosen["set"] == [last]
        assert 0.04 < chosen["p_values"][ridge] < 0.05

    # The plain sentences that rest on the rows checked above.
    for sentence in [
        "both of my paired tests tell the lead of the latest month over the model "
        "apart from chance variation.",
        "Against the figure first published for each month they do not both do so, "
        "although a second method does set the model aside.",
        "the two tests never both support the lead of the model over the same month "
        "last year, whichever figure is used.",
        "told apart from noise when two tests both give a p value below 0.05, "
        "unsettled when only one does and not told apart when neither does.",
        "Wherever the table says told apart, the latest month has the lower miss.",
        "keeps only the latest month on first releases both ways, with the p value "
        "of the model just under 0.05.",
        "A gap that cannot be told apart is not evidence that two methods are equal.",
    ]:
        assert sentence in prose, sentence


def test_local_links_resolve():
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", WALKTHROUGH):
        assert (ROOT / "docs" / target).resolve().is_file(), target
