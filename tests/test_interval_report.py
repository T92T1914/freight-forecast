"""Independent retained interval arithmetic and presentation regressions."""

import copy
import csv
import io
import json
import unittest
import xml.etree.ElementTree as ET

from tools import render_interval_report as report


class IntervalReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = report.load_result()
        cls.protocol = json.loads(
            (report.ROOT / "docs/interval-protocol.json").read_text()
        )
        cls.points = json.loads(
            (report.ROOT / "docs/real-data-example.json").read_text()
        )

    def validate(self, data):
        report.validate(data, self.protocol, self.points)

    def test_retained_months_metrics_and_original_bytes(self):
        self.validate(self.data)
        files = report.render_outputs()
        self.assertEqual(
            files["intervals.json"], (report.ROOT / report.DATA).read_bytes()
        )
        self.assertEqual(len(report.table_rows(self.data)), 9)
        self.assertEqual(len(report.table_rows(self.data, True)), 54)
        self.assertEqual(self.data["groups"][0]["by_year"]["2021"]["covered"], 7)
        metadata = json.loads(files["interval-presentation.json"])
        self.assertEqual(
            metadata["interval_evaluated_revision"],
            "36f6b5212592468d6af36a899f71a422904e1908",
        )
        self.assertEqual(
            metadata["point_evaluated_revision"],
            "45ae5781e91d3a61250aa881e62c2e4eca98a9f4",
        )
        self.assertFalse(metadata["evaluation_rerun"])
        self.assertIn(
            "presentation/intervals.template.html", metadata["renderer_text_sha256_lf"]
        )

    def test_all_csv_rows_retain_exact_numeric_values_and_identities(self):
        rows = list(csv.DictReader(io.StringIO(report.monthly_csv(self.data))))
        self.assertEqual(len(rows), 648)
        i = 0
        for group in self.data["groups"]:
            for source in group["rows"]:
                row = rows[i]
                self.assertEqual(row["point_method"], group["point_method"])
                self.assertEqual(row["interval_method"], group["interval_method"])
                self.assertEqual(row["month"], source["month"])
                for key in (
                    "lower",
                    "upper",
                    "point",
                    "actual",
                    "width",
                    "interval_score",
                ):
                    self.assertEqual(float(row[key]), source[key])
                self.assertEqual(row["covered"], str(source["covered"]))
                i += 1

    def test_tampered_bounds_future_boundary_score_and_alpha_are_rejected(self):
        for field, value in (
            ("lower", 0),
            ("covered", False),
            ("width", 0),
            ("interval_score", 0),
            ("alpha", 0.2),
            ("calibration_last", "2020-01"),
            ("rank", 1),
            ("point", 100),
            ("actual", float("nan")),
        ):
            data = copy.deepcopy(self.data)
            data["groups"][0]["rows"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(data)

    def test_missing_conditions_months_and_years_are_not_zero(self):
        for change in ("condition", "month", "year", "metric"):
            data = copy.deepcopy(self.data)
            if change == "condition":
                data["groups"].pop()
            elif change == "month":
                data["groups"][0]["rows"].pop()
            elif change == "year":
                del data["groups"][0]["by_year"]["2020"]
            else:
                data["groups"][0]["summary"]["coverage"] = 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(data)

    def test_unbounded_summary_and_csv_do_not_turn_infinity_into_zero(self):
        data = copy.deepcopy(self.data)
        row = data["groups"][0]["rows"][0]
        row.update(
            status="unbounded",
            covered=True,
            score_status="infinite",
            lower=None,
            upper=None,
            width=None,
            interval_score=None,
        )
        summary = dict(
            months=1,
            covered=1,
            coverage=1,
            unbounded=1,
            missing=0,
            finite_only_months=0,
            aggregate_status="infinite",
            mean_width=None,
            mean_interval_score=None,
            finite_only_mean_width=None,
            finite_only_mean_interval_score=None,
        )
        report.check_summary(summary, [row])
        summary["mean_width"] = 0
        with self.assertRaises(ValueError):
            report.check_summary(summary, [row])
        exported = next(csv.DictReader(io.StringIO(report.monthly_csv(data))))
        self.assertEqual(exported["status"], "unbounded")
        self.assertEqual(exported["lower"], "")
        self.assertEqual(report.value(None), "infinite")

    def test_chart_editions_keep_domains_labels_geometry_and_metadata(self):
        files = report.render_outputs()
        ns = {"s": "http://www.w3.org/2000/svg"}
        roots = [
            ET.fromstring(files[f"intervals-{mode}.svg"])
            for mode in ("clair", "obscur")
        ]
        self.assertEqual(roots[0].get("data-width-domain"), "0,20")
        self.assertEqual(
            roots[0].get("data-width-domain"), roots[1].get("data-width-domain")
        )
        for root in roots:
            groups = root.findall("s:g[@data-point]", ns)
            self.assertEqual(len(groups), 9)
            self.assertEqual(len(root.findall(".//s:circle", ns)), 6)
            self.assertEqual(len(root.findall(".//s:polygon", ns)), 6)
        for a, b in zip(roots[0].iter(), roots[1].iter(), strict=True):
            if a.tag.endswith("metadata"):
                continue
            self.assertEqual(a.text, b.text)
            self.assertEqual(
                {k: v for k, v in a.attrib.items() if k not in {"fill", "stroke"}},
                {k: v for k, v in b.attrib.items() if k not in {"fill", "stroke"}},
            )
        for mode, root in zip(("clair", "obscur"), roots, strict=True):
            metadata = json.loads(root.find("s:metadata", ns).text)
            self.assertEqual(metadata.pop("appearance"), mode)
            self.assertEqual(metadata, json.loads(files["interval-presentation.json"]))


if __name__ == "__main__":
    unittest.main()
