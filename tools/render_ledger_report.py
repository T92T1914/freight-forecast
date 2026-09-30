"""Render a saved ledger view with the existing report appearance adapter."""

import html
import json
from pathlib import Path

try:
    from .presentation import ROOT, appearance_control
except ImportError:
    from presentation import ROOT, appearance_control


def render_outputs(path: Path | None = None):
    path = path or ROOT / "docs/ledger-fixture.json"
    raw = path.read_bytes()
    result = json.loads(raw)
    if result.get("schema_version") != 1 or result.get("outcome_policy") not in (
        "first_release",
        "latest_available",
    ):
        raise ValueError("unsupported saved ledger view")
    escape = lambda value: html.escape(str(value))  # noqa: E731
    kinds = [row["issuance"]["evidence_kind"] for row in result["rows"]]
    counts = {
        kind: kinds.count(kind)
        for kind in ("real_issuance", "retrospective_replay", "synthetic_fixture")
    }
    if len(kinds) != sum(counts.values()):
        raise ValueError("unknown evidence kind in saved ledger view")
    sections = []
    for kind, title in (
        ("real_issuance", "Real issued predictions"),
        ("retrospective_replay", "Retrospective replays"),
        ("synthetic_fixture", "Synthetic workflow fixtures"),
    ):
        rows = [
            row for row in result["rows"] if row["issuance"]["evidence_kind"] == kind
        ]
        body = []
        for row in rows:
            issued = row["issuance"]
            outcome = row["outcome"]
            value = (
                "Unavailable" if outcome is None else str(outcome["payload"]["value"])
            )
            body.append(
                "<tr>"
                + "".join(
                    f"<td>{escape(item)}</td>"
                    for item in (
                        issued["target_month"],
                        issued["classification"].replace("_", " "),
                        row["status"].replace("_", " "),
                        issued["prediction"],
                        value,
                        issued["last_observation"],
                        issued["seasonal_naive"],
                        issued["issued_at"],
                    )
                )
                + "</tr>"
            )
        empty = (
            "<p>No records in this category. "
            "No prospective accuracy is established.</p>"
        )
        table = (
            "<p>Index points, 2000 average equal to 100. Unavailable is not zero.</p>"
            "<div class='table-wrap' tabindex='0' role='region' "
            "aria-label='Ledger table'>"
            "<table><caption>Saved issuances</caption><thead><tr>"
            "<th scope='col'>Target</th><th scope='col'>Product</th>"
            "<th scope='col'>Status</th><th scope='col'>Prediction</th>"
            "<th scope='col'>Selected outcome</th><th scope='col'>Last observation</th>"
            "<th scope='col'>Seasonal baseline</th><th scope='col'>Issued</th>"
            "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
        )
        sections.append(
            f"<section class='panel' id='{kind}'><h2>{title}</h2>"
            + (table if rows else empty)
            + "</section>"
        )
    cohorts = []
    corrections = [event for event in result["events"] if event["kind"] == "correction"]
    correction_list = "".join(
        f"<li>{escape(event['payload']['reason'])}. Excluded event "
        f"<code>{escape(event['payload']['target_event_id'])}</code>.</li>"
        for event in corrections
    )
    for cohort in result["cohorts"]:
        mae = {
            method: "Not scored" if value is None else value
            for method, value in cohort["mae"].items()
        }
        cohorts.append(
            f"<li>{escape(cohort['evidence_kind'])}, "
            f"{escape(cohort['classification'])}: "
            f"{cohort['scored_targets']} scored target(s). Prediction MAE "
            f"{escape(mae['prediction'])}, last observation "
            f"{escape(mae['last_observation'])}, "
            f"seasonal baseline {escape(mae['seasonal_naive'])}. "
            f"Policy <code>{escape(cohort['policy_id'])}</code>.</li>"
        )
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Freight Forecast | Issuance ledger</title>"
        "<script src='appearance.js'></script>"
        "<link rel='stylesheet' href='appearance.css'>"
        "<link rel='stylesheet' href='style.css'></head>"
        "<body data-project='freight-forecast'>"
        "<a class='skip' href='#main'>Skip to ledger</a><div class='wrap'><nav>"
        "<a href='index.html'>Forecast project</a>"
        "<a href='real-data.html'>Retained BTS evaluation</a>"
        + appearance_control()
        + "</nav><main id='main'><h1>Prospective issuance ledger</h1>"
        "<p class='lead'>The ledger retains issued values, "
        "later observations and corrections. "
        f"This saved view contains {counts['real_issuance']} real issued predictions, "
        f"{counts['retrospective_replay']} retrospective replays and "
        f"{counts['synthetic_fixture']} synthetic workflow fixtures. "
        "Missing outcomes establish no prospective forecasting accuracy.</p>"
        "<p>Future forecasts, elapsed-period nowcasts and retrospective replays "
        "have separate classifications. Latest revised BTS values are not "
        "historical first releases.</p>"
        + "".join(sections)
        + "<p class='fine muted'>Scroll each table to inspect both baselines "
        "and the full issuance clock. Complete identities and source clocks "
        "are retained in the download.</p>"
        + "<section class='panel'><h2>Evaluation boundary</h2>"
        f"<p>Outcome policy: <code>{escape(result['outcome_policy'])}</code>. "
        f"Issuance policy: {escape(result['issuance_policy'])}. "
        "Revisions are not extra forecasts. "
        "Metrics stay separate by product and policy.</p><ul>"
        + "".join(cohorts)
        + "</ul><p>Synthetic errors above only exercise the workflow. "
        "Excluded and missing outcomes remain visible. The original BTS study in which "
        "last observation beat the fitted model is unchanged.</p></section>"
        + f"<details><summary>Corrections retained: {len(corrections)}</summary>"
        "<p>Exclusion changes evaluation while keeping the original record.</p><ul>"
        + correction_list
        + "</ul></details>"
        "<p><a href='ledger.json'>Download event history, "
        "identities and vintages</a> | "
        "<a href='https://github.com/T92T1914/freight-forecast/blob/main/docs/prospective-ledger.md'>"
        "Run the local workflow and inspect its contract</a></p>"
        "<p class='fine muted'>Local timestamps and hashes identify records. "
        "They do not provide "
        "independent timestamping or make a local database tamper-proof. "
        "The page uses installed Inter when available, with a system fallback.</p>"
        "</main></div></body></html>"
    )
    return {"ledger.html": page.encode(), "ledger.json": raw}
