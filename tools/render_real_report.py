"""Render retained BTS results without importing the estimator or re-evaluating it."""

import argparse
import hashlib
import html
import json
import math
import subprocess
from pathlib import Path

try:
    from .presentation import (
        FONT_STACK,
        ROOT,
        appearance_control,
        appearance_css,
        font_css,
        load_tokens,
    )
except ImportError:  # Direct command-line invocation from the source checkout.
    from presentation import (
        FONT_STACK,
        ROOT,
        appearance_control,
        appearance_css,
        font_css,
        load_tokens,
    )

SERIES = ("actual", "model", "last_observation", "seasonal_naive")
METHODS = SERIES[1:]
DATA_PATH = "docs/real-data-example.json"
RENDERER_PATHS = (
    "tools/render_real_report.py",
    "tools/presentation.py",
    "presentation/tokens.json",
    "presentation/real-data.template.html",
    "site/style.css",
    "site/appearance.js",
)
OUTPUTS = {
    "real-data.html",
    "real-data-clair.svg",
    "real-data-obscur.svg",
    "real-data.json",
    "presentation.json",
    "appearance.css",
    "appearance.js",
    "style.css",
}


def text_hash(path):
    """Hash UTF-8 content with LF so checkout line endings are not new evidence."""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def load_result():
    result = json.loads((ROOT / DATA_PATH).read_text(encoding="utf-8"))
    if result["units"] != "index_points_2000_average_100":
        raise ValueError("This renderer expects the retained BTS index result")
    if result["selected_policy"] != "monthly_refit":
        raise ValueError(
            "The report narrative requires the retained monthly-refit policy"
        )
    rows = result["test"]["predictions"]
    expected_months = [
        f"{year}-{month:02}" for year in range(2020, 2026) for month in range(1, 13)
    ]
    if [row["month"] for row in rows] != expected_months:
        raise ValueError("Retained final-test months are missing or reordered")
    if result["test"]["months"] != len(rows):
        raise ValueError("Retained final-test count differs from the rows")
    for row in rows:
        if any(
            isinstance(row[key], bool) or not math.isfinite(row[key]) or row[key] <= 0
            for key in SERIES
        ):
            raise ValueError("Index observations and predictions must be positive")
        if row["observed_through"] >= row["month"]:
            raise ValueError("Observation boundary crosses the target month")
        if row["trained_through"] != row["observed_through"]:
            raise ValueError(
                "Monthly-refit boundary differs from retained observations"
            )
    # Reconcile displayed summaries, without fitting or predicting anything.
    for method in METHODS:
        errors = [row[method] - row["actual"] for row in rows]
        metrics = {
            "mae": sum(abs(error) for error in errors) / len(rows),
            "rmse": math.sqrt(sum(error**2 for error in errors) / len(rows)),
            "mape": sum(
                abs(error) / row["actual"]
                for error, row in zip(errors, rows, strict=True)
            )
            / len(rows),
        }
        for metric, value in metrics.items():
            if not math.isclose(
                result["test"]["metrics"][method][metric],
                value,
                rel_tol=0,
                abs_tol=1e-9,
            ):
                raise ValueError("Retained summary differs from its saved predictions")
    return result


def marker(shape, x, y, color):
    attrs = f'fill="{color}" stroke="{color}"'
    if shape == "circle":
        return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" {attrs}/>'
    if shape == "square":
        return f'<rect x="{x - 3:.2f}" y="{y - 3:.2f}" width="6" height="6" {attrs}/>'
    points = (
        [(x, y - 4), (x + 4, y), (x, y + 4), (x - 4, y)]
        if shape == "diamond"
        else [(x, y - 4), (x + 4, y + 3), (x - 4, y + 3)]
    )
    coords = " ".join(f"{px:.2f},{py:.2f}" for px, py in points)
    return f'<polygon points="{coords}" {attrs}/>'


def chart(result, tokens, appearance=None, record=None):
    """One geometry and stable series identities, with explicit or CSS colors."""
    rows = result["test"]["predictions"]
    lo = math.floor(min(row[key] for row in rows for key in SERIES) / 5) * 5
    hi = math.ceil(max(row[key] for row in rows for key in SERIES) / 5) * 5

    def x(index):
        return 75 + index * 850 / (len(rows) - 1)

    def y(value):
        return 385 - (value - lo) * 285 / (hi - lo)

    def role(name):
        return tokens["themes"][appearance][name] if appearance else f"var(--{name})"

    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 510" '
        f'role="img" aria-labelledby="chart-title chart-desc" data-domain="{lo},{hi}">',
        '<title id="chart-title">BTS freight index, '
        "retained final test 2020 to 2025</title>",
        '<desc id="chart-desc">Actual index and three forecasts on the same 72 months. '
        "Index points, 2000 average equals 100. Lines, markers and the report "
        "data table identify each series. See real-data.html in the same bundle. "
        "Last observation has the lowest mean absolute "
        "error. This revised-data test does not simulate publication dates.</desc>",
        f"<style>{font_css()}text{{font-family:{FONT_STACK};font-synthesis:none}}</style>",
        f'<rect width="960" height="510" fill="{role("panel")}"/>',
        f'<g fill="{role("text")}"><text x="28" y="32" font-size="21" '
        'font-weight="600">A real index, a harder baseline</text>',
        '<text x="28" y="60" font-size="14">Seasonally adjusted index points '
        "(2000 average = 100)</text></g>",
    ]
    if record is not None:
        metadata = dict(record, appearance=appearance or "adaptive")
        pieces.insert(
            1, "<metadata>" + html.escape(json.dumps(metadata)) + "</metadata>"
        )
    for tick in range(lo, hi + 1, 5):
        pieces.append(
            f'<line x1="75" x2="925" y1="{y(tick):.2f}" y2="{y(tick):.2f}" '
            f'stroke="{role("divider")}"/><text x="64" y="{y(tick) + 5:.2f}" '
            f'text-anchor="end" fill="{role("muted")}" font-size="14">{tick}</text>'
        )
    for index in range(0, len(rows), 12):
        pieces.append(
            f'<text x="{x(index):.2f}" y="410" text-anchor="middle" '
            f'fill="{role("muted")}" font-size="14">{rows[index]["month"][:4]}</text>'
        )
    for number, key in enumerate(SERIES):
        series = tokens["series"][key]
        color = series[appearance] if appearance else f"var(--series-{key})"
        path = " ".join(
            f"{'M' if index == 0 else 'L'}{x(index):.2f},{y(row[key]):.2f}"
            for index, row in enumerate(rows)
        )
        pieces.append(
            f'<g data-series="{key}"><path d="{path}" fill="none" '
            f'stroke="{color}" stroke-width="2.2" stroke-dasharray="{series["dash"]}"/>'
        )
        for index in range(0, len(rows), 6):
            pieces.append(
                marker(series["marker"], x(index), y(rows[index][key]), color)
            )
        lx, ly = 32 + (number % 2) * 450, 445 + (number // 2) * 30
        pieces.append(
            f'<path d="M{lx},{ly}h42" stroke="{color}" stroke-width="2.2" '
            f'stroke-dasharray="{series["dash"]}"/>'
        )
        pieces.append(marker(series["marker"], lx + 21, ly, color))
        pieces.append(
            f'<text x="{lx + 52}" y="{ly + 5}" fill="{role("text")}" '
            f'font-size="15">{series["label"]}</text></g>'
        )
    pieces.append("</svg>")
    return "\n".join(pieces)


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
        "kind": "new_presentation_of_retained_results",
        "data": {"path": DATA_PATH, "text_sha256_lf": text_hash(ROOT / DATA_PATH)},
        "evaluated_revision": result["validation_update"]["evaluated_revision"],
        "presentation_revision": revision,
        "presentation_worktree_dirty": dirty,
        "renderer_text_sha256_lf": {
            path: text_hash(ROOT / path) for path in RENDERER_PATHS
        },
        "tokens": tokens["source"],
        "font_delivery": "Local Inter lookup only. System fallback when unavailable.",
        "evaluation_rerun": False,
    }


def document(result, tokens, record):
    esc = html.escape
    series = tokens["series"]
    metrics = result["test"]["metrics"]
    summary = "".join(
        f'<tr data-method="{key}"><th scope="row">{series[key]["label"]}</th>'
        f"<td>{metrics[key]['mae']:.3f}</td><td>{metrics[key]['rmse']:.3f}</td>"
        f"<td>{100 * metrics[key]['mape']:.2f}%</td></tr>"
        for key in METHODS
    )
    rows = "".join(
        f'<tr><th scope="row">{row["month"]}</th><td>{row["trained_through"]}</td>'
        + "".join(
            f'<td data-series="{key}" data-value="{row[key]!r}">{row[key]:.3f}</td>'
            for key in SERIES
        )
        + "</tr>"
        for row in result["test"]["predictions"]
    )
    limits = "".join(f"<li>{esc(limit)}</li>" for limit in result["limitations"])
    source = result["provenance"]["data"]
    evaluated = record["evaluated_revision"]
    presentation = record["presentation_revision"]
    state = (
        " (uncommitted presentation changes)"
        if record["presentation_worktree_dirty"]
        else ""
    )
    template = (ROOT / "presentation/real-data.template.html").read_text(
        encoding="utf-8"
    )
    return template.format(
        appearance_styles=appearance_css(tokens),
        stylesheet=(ROOT / "site/style.css").read_text(encoding="utf-8"),
        control=appearance_control(),
        chart_svg=chart(result, tokens, record=record),
        retrieved=esc(source["retrieved_utc"]),
        catalog=esc(source["catalog_url"], quote=True),
        revision_policy=esc(source["revision_policy_url"], quote=True),
        token_url=tokens["source"]["url"],
        summary=summary,
        rows=rows,
        limits=limits,
        evaluated=evaluated,
        presentation=presentation,
        state=state,
    )


def render_outputs():
    result, tokens = load_result(), load_tokens()
    record = provenance(result, tokens)
    return {
        "real-data.html": document(result, tokens, record).encode(),
        "real-data-clair.svg": chart(result, tokens, "clair", record).encode(),
        "real-data-obscur.svg": chart(result, tokens, "obscur", record).encode(),
        "real-data.json": (ROOT / DATA_PATH).read_bytes(),
        "presentation.json": (json.dumps(record, indent=2) + "\n").encode(),
        "appearance.css": appearance_css(tokens).encode(),
        "appearance.js": (ROOT / "site/appearance.js").read_bytes(),
        "style.css": (ROOT / "site/style.css").read_bytes(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        output == ROOT
        or ROOT in output.parents
        and output.parts[len(ROOT.parts)] not in {"reports", "_site"}
    ):
        parser.error(
            "Use reports/, _site/ or an output directory outside this checkout"
        )
    if output.exists() and any(
        p.name not in OUTPUTS or not p.is_file() or p.is_symlink()
        for p in output.iterdir()
    ):
        parser.error("Output contains files not owned by this renderer")
    files = render_outputs()
    output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (output / name).write_bytes(content)
    print(f"Rendered {len(files)} files from retained data into {output}")


if __name__ == "__main__":
    main()
