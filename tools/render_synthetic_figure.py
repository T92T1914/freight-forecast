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


def draw(data, tokens, mode, files, output, layout="narrow"):
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
        wide = layout == "wide"
        if layout not in ("narrow", "wide"):
            raise ValueError("Unknown figure layout")
        fig = Figure(
            figsize=(9, 5.6) if wide else (4.8, 9), dpi=200, facecolor=roles["canvas"]
        )

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

        label(0.055, 0.96, "FREIGHT FORECAST", 13, "SemiBold", roles["accent"])
        label(0.055, 0.90 if wide else 0.91, "24 test months", 26, "Bold")
        label(
            0.055,
            0.81 if wide else 0.855,
            "Seeded synthetic shipments, 2023 to 2024",
            14 if wide else 12.8,
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
            bbox_to_anchor=(0.055, 0.76 if wide else 0.805),
            prop=FontProperties(fname=str(files["Regular"]), size=13.5),
            frameon=False,
            labelcolor=roles["text"],
            borderaxespad=0,
            labelspacing=0.35,
        )
        # Geometry differs, but axes, all 72 values and series identities do not.
        bounds = (0.075, 0.22, 0.55, 0.32) if wide else (0.12, 0.355, 0.82, 0.305)
        ax = fig.add_axes(bounds, facecolor=roles["canvas"])
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
        ax.tick_params(axis="both", length=0, pad=6, colors=roles["muted"])
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontproperties(fonts["Regular"])
            tick.set_fontsize(14)
        ax.grid(axis="y", color=roles["divider"], linewidth=0.6, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        label(0.055, 0.595 if wide else 0.698, "Moves", 13, "SemiBold", roles["muted"])
        x, y, w, h = (0.695, 0.245, 0.255, 0.44) if wide else (0.055, 0.16, 0.89, 0.122)
        fig.add_artist(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                transform=fig.transFigure,
                boxstyle="round,pad=0.012,rounding_size=0.012",
                facecolor=roles["panel"],
                edgecolor=roles["divider"],
            )
        )
        if wide:
            label(0.72, 0.655, "Mean absolute error", 14, "SemiBold")
            label(0.72, 0.605, "moves / month", 13)
            for yy, key, name, token in [
                (0.51, "model_mae", "Ridge model", "model"),
                (0.37, "naive_mae", "Seasonal baseline", "seasonal_naive"),
            ]:
                label(
                    0.72,
                    yy,
                    f"{data['metrics'][key]:.0f}",
                    27,
                    "Bold",
                    tokens["series"][token][mode],
                )
                label(0.72, yy - 0.08, name, 13)
            label(
                0.055,
                0.105,
                "Trained through Dec 2022. Each forecast uses prior observations. "
                "Seed 2017.",
                13,
            )
            label(
                0.055,
                0.052,
                "Point predictions, no uncertainty intervals. "
                "Not operational forecasts.",
                13,
                "Italic",
                roles["muted"],
            )
        else:
            label(0.085, 0.265, "Mean absolute error, moves / month", 12.6, "SemiBold")
            for xx, key, name, token in [
                (0.085, "model_mae", "model", "model"),
                (0.50, "naive_mae", "seasonal baseline", "seasonal_naive"),
            ]:
                label(
                    xx,
                    0.233,
                    f"{data['metrics'][key]:.0f}",
                    24,
                    "Bold",
                    tokens["series"][token][mode],
                )
                label(xx, 0.19, name, 11.8)
            label(
                0.055,
                0.145,
                "Trained through Dec 2022. Seed 2017.\n"
                "Each forecast uses prior observations.\n"
                "Point predictions, no uncertainty intervals.",
                12.4,
                linespacing=1.45,
            )
            label(
                0.055, 0.045, "Not operational forecasts.", 13, "Italic", roles["muted"]
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
            suffix = "-wide" if wide else ""
            path = output / f"freight-forecast-{mode}{suffix}.{ext}"
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
                f"docs/freight-forecast-{mode}{suffix}.{ext}"
                for mode in ("clair", "obscur")
                for suffix in ("", "-wide")
                for ext in ("png", "svg")
            }
            or digest(ROOT / name) != expected
        ):
            raise ValueError(f"Figure output changed: {name}")
    if len(record["outputs_sha256"]) != 8:
        raise ValueError("Both layouts require PNG and SVG editions")
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
        print("Both layouts and appearances match the retained values and renderer")
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
            for layout in ("narrow", "wide"):
                draw(data, tokens, mode, files, output, layout)
        import matplotlib

        record = {
            "schema_version": 2,
            "layouts": {
                "narrow": {"inches": [4.8, 9], "axes": [0.12, 0.355, 0.82, 0.305]},
                "wide": {"inches": [9, 5.6], "axes": [0.075, 0.22, 0.55, 0.32]},
            },
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
    print(
        "Rendered both layouts and appearances from retained data "
        "with verified Inter files"
    )


if __name__ == "__main__":
    main()
