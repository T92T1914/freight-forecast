"""Sharing metadata must be available before JavaScript runs."""

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tools.render_share_preview import check

ROOT = Path(__file__).resolve().parents[1]


class Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.canonical = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and "property" in values:
            self.meta.setdefault(values["property"], []).append(values.get("content"))
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical.append(values["href"])


class SharingTests(unittest.TestCase):
    def test_checked_image_and_initial_metadata(self):
        config = json.loads((ROOT / "docs/sharing-card.json").read_text())
        receipt = check()
        self.assertFalse(receipt["experimental_work_rerun"])
        self.assertEqual(
            {x["face"] for x in receipt["painted_labels"]},
            {"Inter-Regular", "Inter-SemiBold", "Inter-Bold", "Inter-Italic"},
        )
        for relative, canonical in config["page_urls"].items():
            head = Head()
            head.feed((ROOT / relative).read_text().split("</head>")[0])
            self.assertEqual(head.canonical, [canonical])
            self.assertEqual(head.meta["og:url"], [canonical])
            self.assertEqual(head.meta["og:image"], [config["image_url"]])
            self.assertEqual(head.meta["og:image:width"], ["1280"])
            self.assertEqual(head.meta["og:image:height"], ["640"])
            self.assertEqual(head.meta["og:image:type"], ["image/png"])
            self.assertEqual(head.meta["og:description"], [config["description"]])
            self.assertTrue(head.meta["og:title"][0])
            self.assertTrue(head.meta["og:image:alt"][0])


if __name__ == "__main__":
    unittest.main()
