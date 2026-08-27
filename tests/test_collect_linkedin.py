from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("collect_linkedin", ROOT / "scripts/collect_linkedin.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class LinkedInCollectorTest(unittest.TestCase):
    def test_normalizes_company_and_person_urls(self) -> None:
        company = MODULE.normalize_profile_url("https://linkedin.com/company/alphaess/about/")
        person = MODULE.normalize_profile_url("in/example-person")
        self.assertEqual(company[0], "https://www.linkedin.com/company/alphaess/")
        self.assertEqual(company[1], "https://www.linkedin.com/company/alphaess/posts/?feedView=all")
        self.assertEqual(person[1], "https://www.linkedin.com/in/example-person/recent-activity/all/")

    def test_row_keeps_visible_metrics_and_stable_activity_id(self) -> None:
        row = MODULE.post_to_row(
            {
                "permalink": "https://www.linkedin.com/feed/update/urn:li:activity:7345678901234567890/?trk=x",
                "author": "Example Energy",
                "body": "A public company update",
                "publishedLabel": "2d",
                "metricLabels": ["21 reactions", "4 comments", "2 reposts"],
                "rawText": "Example Energy 2d A public company update 21 reactions 4 comments 2 reposts",
            },
            "Example Energy",
            "example-energy",
            "https://www.linkedin.com/company/example-energy/",
            "2026-08-27T00:00:00+00:00",
        )
        self.assertEqual(row["record_id"], "LI-7345678901234567890")
        self.assertEqual(row["likes"], "21")
        self.assertEqual(row["comments_count"], "4")
        self.assertEqual(row["shares"], "2")
        self.assertEqual(row["published_label"], "2d")

    def test_cli_collects_visible_company_posts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake = temp / "opencli"
            fake.write_text(
                """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[2] == 'eval':
    print(json.dumps({
      'url': 'https://www.linkedin.com/company/example/posts/',
      'title': 'Example Energy | LinkedIn',
      'challenge': False,
      'signedOut': False,
      'scrollY': 0,
      'scrollHeight': 1000,
      'posts': [{
        'permalink': 'https://www.linkedin.com/feed/update/urn:li:activity:7345678901234567890/',
        'author': 'Example Energy',
        'body': 'Visible public update',
        'publishedLabel': '2d',
        'metricLabels': ['21 reactions', '4 comments', '2 reposts'],
        'rawText': 'Visible public update 21 reactions 4 comments 2 reposts'
      }]
    }))
else:
    print(json.dumps({'ok': True}))
""",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            output = temp / "bundle"
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/collect_linkedin.py"),
                    "--company", "Example Energy",
                    "--profile", "https://www.linkedin.com/company/example/",
                    "--opencli", str(fake),
                    "--output", str(output),
                    "--max-scrolls", "0",
                    "--delay", "0",
                    "--no-analysis-packet",
                ],
                check=True, capture_output=True, text=True, cwd=ROOT,
            )
            with (output / "content.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["record_id"], "LI-7345678901234567890")
            self.assertEqual(manifest["collection_controls"]["write_actions"], "none")
            self.assertIn("Captured 1 visible LinkedIn posts", result.stdout)

    def test_project_cli_exposes_linkedin(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "public-web-census"), "linkedin", "--help"],
            check=True, capture_output=True, text=True, cwd=ROOT,
        )
        self.assertIn("--profile", result.stdout)


if __name__ == "__main__":
    unittest.main()
