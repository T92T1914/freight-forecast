"""Check actual committed geometry and public consumers without training."""

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from tools import render_synthetic_figure as renderer
from tools.presentation import ROOT

NS = {"s": "http://www.w3.org/2000/svg"}


class SyntheticFigureTests(unittest.TestCase):
    def test_checked_outputs_belong_to_current_inputs_and_retained_evaluation(self):
        record = renderer.check_outputs()
        self.assertFalse(record["evidence"]["evaluation_rerun"])
        self.assertEqual(record["evidence"]["units"], "moves")
        self.assertEqual(record["evidence"]["axis_domain"], [0, 16000])
        self.assertEqual(len(record["evidence"]["rows"]), 24)
        self.assertEqual(record["evidence"]["trained_through"], "2022-12-01")
        self.assertEqual(
            record["evidence"]["source_revision"],
            "3cd78f7316cd648aad5cc1407529a70131ca397c",
        )
        self.assertEqual(set(record["typography"]["files"]), set(renderer.FACES))

    def test_both_svg_paths_reconcile_to_each_retained_value(self):
        rows = renderer.load_data()["rows"]
        roots = [
            ET.parse(ROOT / f"docs/freight-forecast-{mode}.svg").getroot()
            for mode in ("clair", "obscur")
        ]
        for key in ("actual", "model", "baseline"):
            paths = [
                root.find(f".//s:g[@id='series-{key}']/s:path", NS).get("d")
                for root in roots
            ]
            self.assertEqual(paths[0], paths[1])
            points = re.findall(r"[ML]\s+([\d.-]+)\s+([\d.-]+)", paths[0])
            self.assertEqual(len(points), 24)
            # SVG points are in 72 points/inch. Independently invert the axes.
            for index, ((x, y), row) in enumerate(zip(points, rows, strict=True)):
                month = (float(x) / 345.6 - 0.115) / 0.82 * 23.6 - 0.3
                value = ((1 - 0.317) * 806.4 - float(y)) / (0.26 * 806.4) * 16000
                self.assertAlmostEqual(month, index, places=6)
                self.assertAlmostEqual(value, row[key], places=3)

    def test_svg_contains_real_inter_outlines_and_no_external_fonts(self):
        for mode in ("clair", "obscur"):
            svg = (ROOT / f"docs/freight-forecast-{mode}.svg").read_text()
            for face in ("Regular", "SemiBold", "Bold", "Italic"):
                self.assertIn(f'id="Inter-{face}-', svg)
            self.assertNotIn("DejaVu", svg)
            self.assertNotIn("<text", svg)
            self.assertNotIn("@font-face", svg)
            root = ET.fromstring(svg)
            for node in root.iter():
                for name, value in node.attrib.items():
                    if name.endswith("href"):
                        self.assertTrue(
                            value.startswith("#"),
                            "Only local glyph references are permitted",
                        )

    def test_missing_months_wrong_metrics_and_nonfinite_values_are_rejected(self):
        original = renderer.load_data()
        for kind in ("month", "metric", "nan", "cutoff"):
            data = copy.deepcopy(original)
            if kind == "month":
                data["rows"].pop()
            elif kind == "metric":
                data["metrics"]["model_mae"] = 0
            elif kind == "nan":
                data["rows"][0]["model"] = float("nan")
            else:
                data["trained_through"] = "2024-12-01"
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "docs").mkdir()
                (root / renderer.DATA).write_text(json.dumps(data))
                with (
                    patch.object(renderer, "ROOT", root),
                    self.assertRaises(ValueError),
                ):
                    renderer.load_data()

    def test_render_requires_explicit_fonts_without_downloading_or_writing(self):
        result = subprocess.run(
            [sys.executable, "tools/render_synthetic_figure.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--font-dir is required", result.stderr)

    def test_main_readme_and_notes_actually_use_both_editions(self):
        for path in ("README.md", "docs/visual-example.md"):
            content = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("<picture>", content)
            for mode in ("clair", "obscur"):
                self.assertIn(f"freight-forecast-{mode}.png", content)
        page = (ROOT / "site/index.html").read_text(encoding="utf-8")
        for mode in ("clair", "obscur"):
            self.assertIn(f'src="synthetic-{mode}.png"', page)
            self.assertIn(f'href="synthetic-{mode}.svg"', page)
        self.assertIn('href="example.svg"', page)


if __name__ == "__main__":
    unittest.main()
