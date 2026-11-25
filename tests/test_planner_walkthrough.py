"""Every number in the planner walkthrough appears in the document it quotes."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WALKTHROUGH = (ROOT / "docs/planner-walkthrough.md").read_text(encoding="utf-8")
SOURCES = (
    "README.md",
    "docs/backtesting.md",
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


def test_local_links_resolve():
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", WALKTHROUGH):
        assert (ROOT / "docs" / target).resolve().is_file(), target
