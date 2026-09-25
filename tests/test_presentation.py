"""Presentation checks use saved numbers, with no estimator or serving imports."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from tools import render_real_report as renderer
from tools.presentation import ROOT, appearance_css, load_tokens


def luminance(color):
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels
    ]
    return sum(
        v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def contrast(first, second):
    lo, hi = sorted((luminance(first), luminance(second)))
    return (hi + 0.05) / (lo + 0.05)


class PresentationTests(unittest.TestCase):
    def test_pinned_roles_and_stable_information_ids(self):
        tokens = load_tokens()
        digest = hashlib.sha256(
            json.dumps(tokens["themes"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(
            digest, "d4bff018983dba1c46b19b91111decae07c5eab313e0b12e490561c89d6b9881"
        )
        self.assertEqual(
            tokens["source"]["revision"], "7a57fe750ff50205a17e1d342106a0d3f2777159"
        )
        self.assertEqual(set(tokens["series"]), set(renderer.SERIES))
        self.assertEqual(len({v["marker"] for v in tokens["series"].values()}), 4)
        self.assertEqual(len({v["dash"] for v in tokens["series"].values()}), 4)

    def test_text_focus_and_series_contrast_on_actual_surfaces(self):
        tokens = load_tokens()
        for mode, roles in tokens["themes"].items():
            for background in ("canvas", "panel", "control", "hover", "selected"):
                for foreground in ("text", "muted", "accent"):
                    self.assertGreaterEqual(
                        contrast(roles[foreground], roles[background]),
                        4.5,
                        (mode, foreground, background),
                    )
                self.assertGreaterEqual(contrast(roles["focus"], roles[background]), 3)
            for series in tokens["series"].values():
                self.assertGreaterEqual(contrast(series[mode], roles["panel"]), 3)

    def test_same_points_domains_and_identities_in_both_exports(self):
        result, tokens = renderer.load_result(), load_tokens()
        roots = [
            ET.fromstring(renderer.chart(result, tokens, mode))
            for mode in ("clair", "obscur")
        ]
        self.assertEqual(roots[0].get("data-domain"), roots[1].get("data-domain"))
        ns = {"s": "http://www.w3.org/2000/svg"}
        for key in renderer.SERIES:
            series = [root.find(f"s:g[@data-series='{key}']", ns) for root in roots]
            paths = [group.find("s:path", ns) for group in series]
            self.assertEqual(paths[0].get("d"), paths[1].get("d"))
            self.assertEqual(paths[0].get("d").count("L"), 71)
            self.assertEqual(
                paths[0].get("stroke-dasharray"), paths[1].get("stroke-dasharray")
            )
            self.assertNotEqual(paths[0].get("stroke"), paths[1].get("stroke"))
        self.assertEqual(
            [node.text for node in roots[0].findall(".//s:text", ns)],
            [node.text for node in roots[1].findall(".//s:text", ns)],
        )

    def test_render_preserves_data_bytes_and_experiment_identity(self):
        files = renderer.render_outputs()
        self.assertEqual(
            files["real-data.json"], (ROOT / renderer.DATA_PATH).read_bytes()
        )
        manifest = json.loads(files["presentation.json"])
        self.assertEqual(
            manifest["evaluated_revision"], "45ae5781e91d3a61250aa881e62c2e4eca98a9f4"
        )
        self.assertFalse(manifest["evaluation_rerun"])
        self.assertIn(
            "presentation/real-data.template.html", manifest["renderer_text_sha256_lf"]
        )
        self.assertEqual(
            manifest["data"]["text_sha256_lf"],
            "d1f6b4c631f20809a0d807e66c313a8c97e367c05c5b36253f0378f94fed7582",
        )
        report = files["real-data.html"].decode()
        self.assertEqual(report.count('data-series="model" data-value='), 72)
        for row in renderer.load_result()["test"]["predictions"]:
            for key in renderer.SERIES:
                self.assertIn(f'data-series="{key}" data-value="{row[key]!r}"', report)
        self.assertIn("1.778", report)
        self.assertIn("1.260", report)
        self.assertIn("loses to last observation", report)

    def test_only_local_font_faces_and_explicit_print_override(self):
        css = appearance_css(load_tokens())
        self.assertEqual(css.count("@font-face"), 6)
        self.assertEqual(css.count("src:local("), 6)
        self.assertNotIn("url(", css)
        self.assertIn("@media print{:root,:root[data-appearance]", css)
        self.assertNotIn("filter:", (ROOT / "site/style.css").read_text())

    def test_exports_embed_the_same_experiment_and_presentation_identity(self):
        files = renderer.render_outputs()
        manifest = json.loads(files["presentation.json"])
        for mode in ("clair", "obscur"):
            root = ET.fromstring(files[f"real-data-{mode}.svg"])
            record = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
            self.assertEqual(record.pop("appearance"), mode)
            self.assertEqual(record, manifest)

    def test_missing_nonfinite_or_contradictory_evidence_is_not_drawn_as_zero(self):
        original = renderer.load_result()
        for change in ("missing", "nan", "zero", "metric", "boundary"):
            result = json.loads(json.dumps(original))
            row = result["test"]["predictions"][0]
            if change == "missing":
                del row["actual"]
            elif change == "nan":
                row["model"] = float("nan")
            elif change == "zero":
                row["actual"] = 0
            elif change == "metric":
                result["test"]["metrics"]["model"]["mae"] = 0
            else:
                row["observed_through"] = row["month"]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "docs").mkdir()
                (path / renderer.DATA_PATH).write_text(json.dumps(result))
                with (
                    patch.object(renderer, "ROOT", path),
                    self.assertRaises((ValueError, KeyError)),
                ):
                    renderer.load_result()

    def test_cli_refuses_unowned_output_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            sentinel = path / "unrelated.txt"
            sentinel.write_text("preserve")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/render_real_report.py"),
                    "--output",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertEqual(list(path.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
