"""Draw the retained synthetic example, without importing or running the model."""

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

try:
    from .presentation import ROOT, load_tokens
except ImportError:
    from presentation import ROOT, load_tokens

DATA = "docs/visual-example-data.json"
MANIFEST = "docs/freight-forecast-figure.json"
INPUTS = (DATA, "presentation/tokens.json", "tools/render_synthetic_figure.py")
FACES = {
    "Regular": (400, False),
    "SemiBold": (600, False),
    "Bold": (700, False),
    "Italic": (400, True),
    "SemiBoldItalic": (600, True),
    "BoldItalic": (700, True),
}
SERIES = {
    "actual": ("Actual synthetic volume", "actual", "o", "-"),
    "model": ("Ridge model", "model", "s", "--"),
    "baseline": ("Same month last year", "seasonal_naive", "^", ":"),
}


def digest(path, text=False):
    data = path.read_text(encoding="utf-8").encode() if text else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def load_data():
    data = json.loads((ROOT / DATA).read_text(encoding="utf-8"))
    rows = data["rows"]
    months = [f"{year}-{month:02}" for year in (2023, 2024) for month in range(1, 13)]
    if [r["month"] for r in rows] != months:
        raise ValueError("The figure requires all 24 retained months in order")
    if data["trained_through"] != "2022-12-01":
        raise ValueError("The figure requires the recorded December 2022 cutoff")
    for row, original in zip(
        rows, data["provenance"]["evaluation"]["rows"], strict=True
    ):
        expected = dict(
            month=original["date"][:7],
            actual=original["actual"],
            model=original["prediction"],
            baseline=original["naive"],
        )
        if row != expected or any(
            isinstance(row[key], bool) or not math.isfinite(row[key]) or row[key] < 0
            for key in SERIES
        ):
            raise ValueError("Figure values differ from the saved evaluation")
    for key, metric in (("model", "model_mae"), ("baseline", "naive_mae")):
        mae = sum(abs(r[key] - r["actual"]) for r in rows) / len(rows)
        if not math.isclose(mae, data["metrics"][metric], rel_tol=0, abs_tol=1e-9):
            raise ValueError("Figure metric differs from its retained rows")
    if data["data_type"] != "seeded synthetic shipments":
        raise ValueError("This figure does not describe other datasets")
    return data


def font_files(directory):
    """Resolve actual static faces, never silently render with a fallback."""
    from fontTools.ttLib import TTFont

    files, evidence = {}, {}
    for name, (weight, italic) in FACES.items():
        path = directory / f"Inter-{name}.ttf"
        with TTFont(path) as font:
            names = font["name"]
            postscript = names.getDebugName(6)
            if (
                postscript != f"Inter-{name}"
                or font["OS/2"].usWeightClass != weight
                or bool(font["OS/2"].fsSelection & 1) != italic
            ):
                raise ValueError(f"Incorrect Inter face: {path.name}")
            # Every authored label is ASCII. Prevent silent glyph substitution.
            if not set(range(32, 127)).issubset(font.getBestCmap()):
                raise ValueError(f"Missing Latin figure glyphs: {path.name}")
            evidence[name] = {
                "sha256": digest(path),
                "postscript": postscript,
                "weight": weight,
                "italic": italic,
            }
        files[name] = path
    return files, evidence


def semantic_record(data):
    return {
        "source_revision": data["source_commit"],
        "evaluation_rerun": False,
        "units": "moves",
        "axis_domain": [0, 16000],
        "trained_through": data["trained_through"],
        "evaluation": data["evaluation"],
        "metrics": data["metrics"],
        "rows": data["rows"],
    }


def draw(data, tokens, mode, files, output):
    # Optional authoring dependencies stay outside the service and site builder.
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.text import Text

    roles = tokens["themes"][mode]
    fonts = {name: FontProperties(fname=str(path)) for name, path in files.items()}
    record = semantic_record(data)
    with rc_context({"svg.fonttype": "path", "svg.hashsalt": "freight-synthetic-v1"}):
        # A vertical composition keeps labels useful inside a narrow README.
        fig = Figure(figsize=(4.8, 11.2), dpi=200, facecolor=roles["canvas"])

        def label(x, y, text, size=15, face="Regular", color=None, **kwargs):
            return fig.text(
                x,
                y,
                text,
                fontsize=size,
                fontproperties=fonts[face],
                color=color or roles["text"],
                va="top",
                **kwargs,
            )

        label(0.065, 0.955, "FREIGHT FORECAST", 13, "SemiBold", roles["accent"])
        label(
            0.065,
            0.916,
            "Does the model\nearn its place?",
            28,
            "Bold",
            linespacing=1.05,
        )
        label(0.065, 0.816, "24 test months on\nsynthetic shipments", 15)
        label(
            0.065,
            0.758,
            "One fixed model, with prior\nobservations each month.",
            12,
            color=roles["muted"],
        )
        legend = [
            Line2D(
                [],
                [],
                color=tokens["series"][token][mode],
                marker=marker,
                linestyle=style,
                linewidth=2.2,
                markersize=5,
                label=name,
            )
            for name, token, marker, style in SERIES.values()
        ]
        fig.legend(
            handles=legend,
            loc="upper left",
            bbox_to_anchor=(0.065, 0.700),
            prop=FontProperties(fname=str(files["Regular"]), size=13),
            frameon=False,
            labelcolor=roles["text"],
            borderaxespad=0,
            labelspacing=0.55,
        )
        ax = fig.add_axes((0.115, 0.317, 0.82, 0.26), facecolor=roles["canvas"])
        for key, (name, token, marker, style) in SERIES.items():
            ax.plot(
                range(24),
                [r[key] for r in data["rows"]],
                label=name,
                color=tokens["series"][token][mode],
                marker=marker,
                linestyle=style,
                linewidth=1.9,
                markersize=3.8,
                zorder=4 if key == "actual" else 3,
                gid=f"series-{key}",
            )
        ax.set_ylim(0, 16000)
        ax.set_xlim(-0.3, 23.3)
        ax.set_yticks([0, 4000, 8000, 12000, 16000], ["0", "4k", "8k", "12k", "16k"])
        ax.set_xticks(
            [0, 6, 12, 18, 23], ["Jan\n2023", "Jul", "Jan\n2024", "Jul", "Dec"]
        )
        ax.tick_params(axis="both", length=0, pad=8, colors=roles["muted"])
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontproperties(fonts["Regular"])
            tick.set_fontsize(12)
        ax.grid(axis="y", color=roles["divider"], linewidth=0.6, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        label(0.065, 0.607, "Moves", 13, "SemiBold", roles["muted"])
        box = FancyBboxPatch(
            (0.065, 0.11),
            0.87,
            0.138,
            transform=fig.transFigure,
            boxstyle="round,pad=0.012,rounding_size=0.012",
            facecolor=roles["panel"],
            edgecolor=roles["divider"],
        )
        fig.add_artist(box)
        label(0.095, 0.232, "Mean absolute error, moves / month", 12, "SemiBold")
        label(
            0.095,
            0.201,
            f"{data['metrics']['model_mae']:.0f}",
            27,
            "Bold",
            tokens["series"]["model"][mode],
        )
        label(0.34, 0.19, "model", 14)
        label(
            0.095,
            0.158,
            f"{data['metrics']['naive_mae']:.0f}",
            27,
            "Bold",
            tokens["series"]["seasonal_naive"][mode],
        )
        label(0.34, 0.147, "seasonal baseline", 14)
        label(
            0.065,
            0.094,
            "Trained through December 2022.",
            11.8,
            color=roles["muted"],
        )
        label(
            0.065,
            0.071,
            "Test: 2023 to 2024. Seed 2017.\n"
            "Point predictions, no uncertainty intervals.",
            11.8,
            color=roles["muted"],
        )
        label(
            0.065,
            0.030,
            "Not operational forecasts.",
            11.8,
            "Italic",
            roles["muted"],
        )
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        for item in fig.findobj(Text):
            if item.get_visible() and item.get_text().strip():
                bounds = item.get_window_extent(canvas.get_renderer())
                if (
                    bounds.x0 < 5
                    or bounds.y0 < 5
                    or bounds.x1 > fig.bbox.width - 5
                    or bounds.y1 > fig.bbox.height - 5
                ):
                    raise ValueError(
                        f"Label leaves the figure canvas: {item.get_text()!r}"
                    )
        for ext in ("png", "svg"):
            metadata = {"Description": json.dumps(record, sort_keys=True)}
            if ext == "svg":
                metadata["Date"] = None
            path = output / f"freight-forecast-{mode}.{ext}"
            fig.savefig(path, metadata=metadata)
            if ext == "svg":
                # Canonicalize this authored XML before hashing, including on Windows.
                path.write_text(
                    "\n".join(line.rstrip() for line in path.read_text().splitlines())
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
        fig.clear()


def check_outputs():
    data = load_data()
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(data):
        raise ValueError("Figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Figure input changed: {name}")
    for name, expected in record["outputs_sha256"].items():
        if (
            name
            not in {
                f"docs/freight-forecast-{mode}.{ext}"
                for mode in ("clair", "obscur")
                for ext in ("png", "svg")
            }
            or digest(ROOT / name) != expected
        ):
            raise ValueError(f"Figure output changed: {name}")
    if len(record["outputs_sha256"]) != 4:
        raise ValueError("Both PNG and SVG editions are required")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font-dir", type=Path, help="Directory of six official static Inter TTFs"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify checked outputs without fonts or rendering",
    )
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both figure editions match the retained values and renderer")
        return
    if args.font_dir is None:
        parser.error(
            "--font-dir is required for rendering; no font download is performed"
        )
    data, tokens = load_data(), load_tokens()
    files, font_evidence = font_files(args.font_dir)
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        for mode in ("clair", "obscur"):
            draw(data, tokens, mode, files, output)
        import matplotlib

        record = {
            "schema_version": 1,
            "evidence": semantic_record(data),
            "renderer": {"matplotlib": matplotlib.__version__, "backend": "Agg"},
            "typography": {
                "files": font_evidence,
                "painted_faces": ["Regular", "SemiBold", "Bold", "Italic"],
                "png": "Glyphs rasterized from explicit Inter files",
                "svg": (
                    "Labels outlined from the same Inter files. Source values remain "
                    "selectable in JSON and the public table"
                ),
            },
            "inputs_sha256_lf": {
                name: digest(ROOT / name, text=True) for name in INPUTS
            },
            "outputs_sha256": {
                "docs/" + p.name: digest(p) for p in sorted(output.iterdir())
            },
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    check_outputs()
    print("Rendered both figures from retained data with verified Inter files")


if __name__ == "__main__":
    main()
