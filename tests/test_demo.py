from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DemoTest(unittest.TestCase):
    def test_demo_builds_evidence_linked_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.html"
            summary_path = Path(tmp) / "summary.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_demo.py"),
                    "--output",
                    str(report),
                    "--json",
                    str(summary_path),
                    "--quiet",
                ],
                check=True,
                cwd=ROOT,
            )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            rendered = report.read_text(encoding="utf-8")

            self.assertEqual(summary["content_rows"], 18)
            self.assertEqual(summary["comment_rows"], 16)
            self.assertEqual(summary["top_topic"], "price")
            self.assertEqual(summary["deep_dive_channels"], 3)
            self.assertIn("Evidence ledger", rendered)
            self.assertIn("Fictional", rendered)
            self.assertIn("NS-010", rendered)

    def test_fictional_bundle_can_enter_versioned_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            bundle = ROOT / "demo" / "input"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "public-web-census"),
                    "discover",
                    "--workspace",
                    str(workspace),
                    "--target",
                    "Northstar Home Energy",
                ],
                check=True,
                cwd=ROOT,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "public-web-census"),
                    "refresh",
                    "--workspace",
                    str(workspace),
                    "--bundle",
                    str(bundle),
                ],
                check=True,
                cwd=ROOT,
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "public-web-census"),
                    "validate",
                    "--workspace",
                    str(workspace),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertTrue(json.loads(validation.stdout)["valid"])
            self.assertEqual(
                json.loads((workspace / "current" / "run_manifest.json").read_text(encoding="utf-8"))[
                    "counts"
                ]["content"],
                18,
            )


if __name__ == "__main__":
    unittest.main()
