"""Audit and present saved residual intervals without fitting or forecasting."""

import argparse
import csv
import html
import io
import json
import math
import re
import subprocess
from fractions import Fraction

try:
    from .presentation import (
        FONT_STACK,
        ROOT,
        appearance_control,
        font_css,
        load_tokens,
    )
    from .render_real_report import marker, text_hash
except ImportError:
    from render_real_report import marker, text_hash

    from presentation import FONT_STACK, ROOT, appearance_control, font_css, load_tokens

DATA = "docs/interval-results.json"
POINTS = ("model", "last_observation", "seasonal_naive")
METHODS = ("fixed", "rolling", "adaptive")
LABELS = {
    "model": "Ridge",
    "last_observation": "Last observation",
    "seasonal_naive": "Seasonal baseline",
}
SHAPES = dict(zip(METHODS, ("circle", "square", "diamond"), strict=True))
PATHS = (
    "tools/render_interval_report.py",
    "tools/presentation.py",
    "tools/render_real_report.py",
    "presentation/tokens.json",
    "presentation/intervals.template.html",
    "site/style.css",
    "site/appearance.js",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label):
    require(
        type(actual) in (int, float)
        and math.isfinite(actual)
        and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-10),
        f"{label} differs from saved observations",
    )


def month(index):
    year, number = divmod(index, 12)
    return f"{year:04}-{number + 1:02}"


def check_summary(summary, rows):
    finite = [r for r in rows if r["status"] == "finite"]
    unbounded = len(rows) - len(finite)
    covered = sum(r["covered"] for r in rows)
    require(
        summary["months"] == len(rows)
        and summary["covered"] == covered
        and summary["unbounded"] == unbounded
        and summary["missing"] == 0
        and summary["finite_only_months"] == len(finite),
        "summary counts differ",
    )
    close(summary["coverage"], covered / len(rows), "coverage")
    require(
        summary["aggregate_status"] == ("infinite" if unbounded else "finite"),
        "infinite status differs",
    )
    for field, key in (
        ("width", "mean_width"),
        ("interval_score", "mean_interval_score"),
    ):
        average = math.fsum(r[field] for r in finite) / len(finite) if finite else None
        if unbounded:
            require(summary[key] is None, "infinite aggregate was replaced")
        else:
            close(summary[key], average, key)
        if average is None:
            require(
                summary["finite_only_" + key] is None, "empty finite subset has mean"
            )
        else:
            close(summary["finite_only_" + key], average, "finite subset " + key)


def validate(result, protocol, points):
    """Reconcile issued bounds and metrics from the retained input, independently."""
    try:
        require(
            result["schema_version"] == 1 and result["protocol"] == protocol,
            "protocol identity differs",
        )
        require(
            result["refitting_performed"] is False
            and result["kind"] == "retrospective_intervals_on_retained_point_forecasts"
            and result["units"] == "index_points_2000_average_100",
            "wrong experiment or units",
        )
        require(
            result["evaluated_revision"] == "36f6b5212592468d6af36a899f71a422904e1908",
            "unexpected measured revision",
        )
        require(
            result["point_forecast_source"]["evaluated_revision"]
            == points["validation_update"]["evaluated_revision"],
            "point evaluation identity differs",
        )
        require(
            result["point_forecast_source"]["sha256_lf"] == protocol["input_sha256_lf"],
            "point input identity differs",
        )
        require(
            [(g["point_method"], g["interval_method"]) for g in result["groups"]]
            == [(p, m) for p in POINTS for m in METHODS],
            "missing interval condition",
        )
        calibration = points["development"]["monthly_refit"]["predictions"]
        calibration = [r for r in calibration if "2015-01" <= r["month"] <= "2019-12"]
        actuals = points["test"]["predictions"]
        require(
            [r["month"] for r in calibration]
            == [month(2015 * 12 + i) for i in range(60)],
            "calibration calendar differs",
        )
        require(
            [r["month"] for r in actuals] == [month(2020 * 12 + i) for i in range(72)],
            "evaluation calendar differs",
        )
        for group in result["groups"]:
            key, method = group["point_method"], group["interval_method"]
            rows, misses = group["rows"], 0
            require(len(rows) == 72, "missing monthly intervals")
            initial_errors = [abs(r["actual"] - r[key]) for r in calibration]
            for i, (row, point) in enumerate(zip(rows, actuals, strict=True)):
                require(
                    row["month"] == point["month"]
                    and row["point"] == point[key]
                    and row["actual"] == point["actual"],
                    "retained point data changed",
                )
                alpha = Fraction(1, 10)
                if method == "adaptive":
                    alpha += Fraction(1, 200) * (Fraction(i, 10) - misses)
                close(row["alpha"], float(alpha), "issued alpha")
                # Audit the saved prefix, never the target or future residuals.
                errors = (
                    initial_errors
                    if method == "fixed"
                    else (
                        initial_errors
                        + [abs(p["actual"] - p[key]) for p in actuals[:i]]
                    )[-60:]
                )
                rank = math.ceil(61 * (1 - alpha))
                first = 2015 * 12 + (0 if method == "fixed" else i)
                require(
                    row["rank"] == rank
                    and row["calibration_count"] == 60
                    and row["calibration_first"] == month(first)
                    and row["calibration_last"] == month(first + 59),
                    "calibration boundary or rank differs",
                )
                if rank > 60:
                    require(
                        row["status"] == "unbounded"
                        and row["covered"] is True
                        and row["score_status"] == "infinite"
                        and all(
                            row[k] is None
                            for k in ("lower", "upper", "width", "interval_score")
                        ),
                        "unbounded interval was hidden",
                    )
                else:
                    radius = 0 if rank < 1 else sorted(errors)[rank - 1]
                    lower, upper = point[key] - radius, point[key] + radius
                    close(row["lower"], lower, "lower bound")
                    close(row["upper"], upper, "upper bound")
                    covered = lower <= point["actual"] <= upper
                    require(
                        row["status"] == row["score_status"] == "finite"
                        and row["covered"] is covered,
                        "coverage or status differs",
                    )
                    close(row["width"], upper - lower, "width")
                    score = (
                        upper
                        - lower
                        + 20 * max(lower - point["actual"], point["actual"] - upper, 0)
                    )
                    close(row["interval_score"], score, "nominal interval score")
                misses += not row["covered"]
            check_summary(group["summary"], rows)
            require(
                list(group["by_year"]) == [str(y) for y in range(2020, 2026)],
                "missing calendar year",
            )
            for year, summary in group["by_year"].items():
                check_summary(summary, [r for r in rows if r["month"].startswith(year)])
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"malformed retained intervals: {exc}") from exc


def load_result():
    result = json.loads((ROOT / DATA).read_text())
    protocol = json.loads((ROOT / "docs/interval-protocol.json").read_text())
    points = json.loads((ROOT / "docs/real-data-example.json").read_text())
    require(
        text_hash(ROOT / "docs/real-data-example.json") == protocol["input_sha256_lf"],
        "retained forecast bytes changed",
    )
    hashes = result["implementation_sha256_lf"]
    require(
        set(hashes)
        == {
            "src/intervals.py",
            "tools/evaluate_intervals.py",
            "docs/interval-protocol.json",
        }
        and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()),
        "invalid measured source identities",
    )
    require(
        text_hash(ROOT / "docs/interval-protocol.json")
        == hashes["docs/interval-protocol.json"],
        "declared protocol changed",
    )
    validate(result, protocol, points)
    return result


def value(number):
    return "infinite" if number is None else f"{number:.3f}"


def table_rows(result, yearly=False):
    rows = []
    for group in result["groups"]:
        summaries = (
            group["by_year"].items() if yearly else [("2020 to 2025", group["summary"])]
        )
        for period, s in summaries:
            rows.append(
                [
                    LABELS[group["point_method"]],
                    group["interval_method"],
                    period,
                    f"{s['covered']}/{s['months']}",
                    f"{100 * s['coverage']:.2f}%",
                    value(s["mean_width"]),
                    value(s["mean_interval_score"]),
                    s["unbounded"],
                    s["missing"],
                ]
            )
    return rows


HEADERS = [
    "Point forecast",
    "Interval",
    "Period",
    "Covered",
    "Coverage",
    "Mean width",
    "Mean interval score",
    "Unbounded",
    "Missing",
]


def table(result, yearly=False):
    name = "yearly" if yearly else "overall"
    caption = "Every calendar year" if yearly else "All nine declared conditions"
    pieces = [
        f'<div class="table-wrap" tabindex="0" role="region" '
        f'aria-label="{caption}"><table id="{name}"><caption>{caption}. '
        "Width and interval score use index points. Lower score is better.</caption>",
        "<thead><tr>"
        + "".join(f'<th scope="col">{h}</th>' for h in HEADERS)
        + "</tr></thead><tbody>",
    ]
    for row in table_rows(result, yearly):
        pieces.append(
            "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>"
        )
    return "\n".join(pieces + ["</tbody></table></div>"])


def chart(result, tokens, appearance=None, record=None):
    def role(name):
        return tokens["themes"][appearance][name] if appearance else f"var(--{name})"

    finite_widths = [
        g["summary"]["mean_width"]
        for g in result["groups"]
        if g["summary"]["mean_width"] is not None
    ]
    maximum = max(5, math.ceil(max(finite_widths, default=0) / 5) * 5)
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 680" '
        'role="img" aria-labelledby="interval-title interval-desc" '
        f'data-width-domain="0,{maximum}">',
        '<title id="interval-title">Retrospective interval coverage and width</title>',
        '<desc id="interval-desc">Nine conditions compare coverage against a 90 '
        "percent target and mean interval width in index points. Higher coverage "
        "can cost greater width. Every value also appears in the report table. "
        "Color identifies the point forecast. Shape and text identify the interval "
        "method. See intervals.html in the same report bundle.</desc>",
        "<metadata>"
        + html.escape(
            json.dumps(dict(record or {}, appearance=appearance or "adaptive"))
        )
        + "</metadata>",
        f"<style>{font_css()}text{{font-family:{FONT_STACK};font-synthesis:none}}</style>",
        f'<rect width="960" height="680" fill="{role("panel")}"/>',
        f'<g fill="{role("text")}"><text x="24" y="34" '
        'font-size="22" font-weight="600">',
        'Coverage has a width cost</text><text x="24" y="65" font-size="15">',
        "Saved BTS forecasts, 72 months per condition, 2020 to 2025</text></g>",
    ]
    for tick in range(0, 101, 25):
        x = 350 + tick * 2.5
        pieces.append(
            f'<line x1="{x}" x2="{x}" y1="120" y2="570" stroke="{role("divider")}"/>'
            f'<text x="{x}" y="595" text-anchor="middle" '
            f'font-size="14" fill="{role("text")}">{tick}</text>'
        )
    for tick in range(0, maximum + 1, 5):
        x = 700 + tick * 220 / maximum
        pieces.append(
            f'<line x1="{x}" x2="{x}" y1="120" y2="570" stroke="{role("divider")}"/>'
            f'<text x="{x}" y="595" text-anchor="middle" '
            f'font-size="14" fill="{role("text")}">{tick}</text>'
        )
    pieces.append(
        f'<path d="M575,120V570" fill="none" stroke="{role("text")}" '
        'stroke-dasharray="5 4"/>'
        f'<g fill="{role("text")}" font-size="16" font-weight="600">'
        '<text x="350" y="100">Coverage (%)</text>'
        '<text x="700" y="100">Mean width</text></g>'
    )
    for i, group in enumerate(result["groups"]):
        key, method, summary = (
            group["point_method"],
            group["interval_method"],
            group["summary"],
        )
        color = (
            tokens["series"][key][appearance] if appearance else f"var(--series-{key})"
        )
        y = 145 + i * 50
        pieces.append(
            f'<g data-point="{key}" data-interval="{method}">'
            f'<text x="24" y="{y + 5}" fill="{role("text")}" font-size="16">'
            f"{LABELS[key]} / {method}</text>"
        )
        pieces.append(marker(SHAPES[method], 350 + summary["coverage"] * 250, y, color))
        width = summary["mean_width"]
        if width is None:
            pieces.append(
                f'<text x="700" y="{y + 5}" font-size="15" '
                f'fill="{color}">infinite</text>'
            )
        else:
            pieces.append(marker(SHAPES[method], 700 + width * 220 / maximum, y, color))
        pieces.append("</g>")
    pieces += [
        f'<g fill="{role("text")}" font-size="14">'
        '<text x="24" y="630">Circle: fixed   Square: rolling   '
        'Diamond: adaptive</text><text x="24" y="656">'
        "Dashed line: nominal 90% target. Width is in index points, "
        "not shipment counts.</text></g>",
        "</svg>",
    ]
    return "\n".join(pieces)


def monthly_csv(result):
    stream = io.StringIO(newline="")
    fields = ["point_method", "interval_method", *result["groups"][0]["rows"][0]]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for group in result["groups"]:
        for row in group["rows"]:
            writer.writerow(
                dict(
                    point_method=group["point_method"],
                    interval_method=group["interval_method"],
                    **row,
                )
            )
    return stream.getvalue()


def provenance(result, tokens):
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()
    )
    return {
        "schema_version": 1,
        "kind": "presentation_of_retained_interval_results",
        "data_text_sha256_lf": text_hash(ROOT / DATA),
        "interval_evaluated_revision": result["evaluated_revision"],
        "point_evaluated_revision": result["point_forecast_source"][
            "evaluated_revision"
        ],
        "protocol_sha256_lf": result["implementation_sha256_lf"][
            "docs/interval-protocol.json"
        ],
        "presentation_revision": revision,
        "presentation_worktree_dirty": dirty,
        "renderer_text_sha256_lf": {p: text_hash(ROOT / p) for p in PATHS},
        "tokens": tokens["source"],
        "evaluation_rerun": False,
        "font_delivery": "Local Inter only. System fallback when unavailable.",
    }


def render_outputs():
    result, tokens = load_result(), load_tokens()
    record = provenance(result, tokens)
    template = (ROOT / "presentation/intervals.template.html").read_text()
    document = template.format(
        control=appearance_control(),
        overall=table(result),
        yearly=table(result, True),
        chart=chart(result, tokens, record=record),
        interval_revision=record["interval_evaluated_revision"],
        point_revision=record["point_evaluated_revision"],
        presentation_revision=record["presentation_revision"],
        dirty=" (uncommitted presentation changes)"
        if record["presentation_worktree_dirty"]
        else "",
        limits="".join(
            f"<li>{html.escape(s)}</li>" for s in result["protocol"]["limits"]
        ),
    )
    return {
        "intervals.html": document.encode(),
        "intervals-clair.svg": chart(result, tokens, "clair", record).encode(),
        "intervals-obscur.svg": chart(result, tokens, "obscur", record).encode(),
        "intervals.json": (ROOT / DATA).read_bytes(),
        "intervals.csv": monthly_csv(result).encode(),
        "interval-protocol.json": (ROOT / "docs/interval-protocol.json").read_bytes(),
        "interval-presentation.json": (json.dumps(record, indent=2) + "\n").encode(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Audit saved data without generating files"
    )
    args = parser.parse_args()
    if not args.check:
        parser.error("Use tools/build_site.py for the complete report bundle")
    result = load_result()
    print(
        f"Validated {sum(len(g['rows']) for g in result['groups'])} "
        "saved intervals without refitting."
    )


if __name__ == "__main__":
    main()
