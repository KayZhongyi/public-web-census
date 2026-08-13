from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_facebook  # noqa: E402


class FacebookCollectorTest(unittest.TestCase):
    def test_normalizes_page_and_post_urls(self) -> None:
        self.assertEqual(
            collect_facebook.normalize_page_url(
                "https://m.facebook.com/ExamplePage/?ref=page_internal"
            ),
            "https://www.facebook.com/ExamplePage",
        )
        self.assertEqual(
            collect_facebook.normalize_page_url(
                "https://facebook.com/profile.php?id=12345&ref=bookmarks"
            ),
            "https://www.facebook.com/profile.php?id=12345",
        )
        with self.assertRaises(ValueError):
            collect_facebook.normalize_page_url("https://example.com/ExamplePage")

        canonical = collect_facebook.canonicalize_post_url(
            "https://m.facebook.com/ExamplePage/posts/12345/?ref=share&mibextid=x"
        )
        self.assertEqual(canonical, "https://www.facebook.com/ExamplePage/posts/12345")
        self.assertEqual(collect_facebook.post_key(canonical), "12345")

    def test_metric_parsing_keeps_blank_fields_distinct_from_zero(self) -> None:
        labels = ["1.2K reactions", "34 comments", "5 shares"]
        self.assertEqual(collect_facebook.metric_from_labels(labels, "likes"), "1200")
        self.assertEqual(collect_facebook.metric_from_labels(labels, "comments"), "34")
        self.assertEqual(collect_facebook.metric_from_labels(labels, "shares"), "5")
        self.assertEqual(collect_facebook.metric_from_labels(["Public post"], "likes"), "")

    def test_cli_accumulates_virtualized_windows_and_writes_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake_opencli = temp / "opencli"
            fake_opencli.write_text(
                """#!/usr/bin/env python3
import json
import pathlib
import sys

state_file = pathlib.Path(__file__).with_suffix('.state')
args = sys.argv[1:]
if args == ['--version']:
    print('opencli 9.9.9-test')
    raise SystemExit(0)

command = args[2]
if command in {'open', 'bind', 'wait', 'close', 'unbind'}:
    print(json.dumps({'ok': True}))
    raise SystemExit(0)
if command == 'scroll':
    current = int(state_file.read_text() if state_file.exists() else '0')
    state_file.write_text(str(current + 1))
    print(json.dumps({'scrolled': True}))
    raise SystemExit(0)
if command != 'eval':
    print(json.dumps({'error': 'unsupported command'}))
    raise SystemExit(2)

round_index = int(state_file.read_text() if state_file.exists() else '0')
posts = [
    {
        'permalink': 'https://www.facebook.com/ExamplePage/posts/100?ref=share',
        'text': 'First public post',
        'publishedAt': '2026-08-01T00:00:00.000Z',
        'publishedLabel': 'August 1',
        'metricLabels': ['1.2K reactions', '34 comments', '5 shares'],
    }
]
if round_index >= 1:
    posts.append({
        'permalink': 'https://m.facebook.com/ExamplePage/posts/200/?mibextid=x',
        'text': 'Second public post',
        'publishedAt': '2026-08-02T00:00:00.000Z',
        'publishedLabel': 'August 2',
        'metricLabels': ['80 reactions', '9 comments'],
    })
print(json.dumps({
    'url': 'https://www.facebook.com/ExamplePage',
    'title': 'Example Page | Facebook',
    'scrollY': round_index * 2400,
    'scrollHeight': 5000 + round_index * 1000,
    'challenge': False,
    'articles': posts,
}))
""",
                encoding="utf-8",
            )
            fake_opencli.chmod(0o755)
            output = temp / "bundle"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_facebook.py"),
                    "--company",
                    "Example Company",
                    "--page",
                    "https://facebook.com/ExamplePage",
                    "--opencli",
                    str(fake_opencli),
                    "--output",
                    str(output),
                    "--max-scrolls",
                    "2",
                    "--delay",
                    "0",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )

            with (output / "content.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            report = (output / "report.html").read_text(encoding="utf-8")

            self.assertEqual({row["record_id"] for row in rows}, {"FB-100", "FB-200"})
            first = next(row for row in rows if row["record_id"] == "FB-100")
            self.assertEqual(first["likes"], "1200")
            self.assertEqual(first["comments_count"], "34")
            self.assertEqual(first["shares"], "5")
            self.assertEqual(first["text_translation"], "")
            self.assertEqual(first["content_type"], "unclassified")
            self.assertEqual(manifest["collector"]["browser_bridge"], "OpenCLI")
            self.assertEqual(manifest["collector"]["browser_bridge_version"], "9.9.9")
            self.assertEqual(manifest["counts"]["unique_content"], 2)
            self.assertEqual(manifest["collection_controls"]["write_actions"], "none")
            self.assertEqual(manifest["status"], "scroll_limit_reached")
            self.assertIn("content classification are intentionally pending", report)
            self.assertTrue((output / "analysis/analysis_task.md").exists())
            self.assertIn("Captured 2 unique public Facebook post records", result.stdout)

    def test_project_cli_exposes_facebook_command(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "competitor-census"), "facebook", "--help"],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertIn("--page", result.stdout)
        self.assertIn("--resume", result.stdout)

    def test_human_verification_writes_checkpoint_but_not_final_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake_opencli = temp / "opencli"
            fake_opencli.write_text(
                """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args == ['--version']:
    print('opencli 9.9.9-test')
elif args[2] in {'open', 'close'}:
    print(json.dumps({'ok': True}))
elif args[2] == 'eval':
    print(json.dumps({
        'url': 'https://www.facebook.com/checkpoint/',
        'title': 'Security check',
        'scrollY': 0,
        'scrollHeight': 1000,
        'challenge': True,
        'articles': [],
    }))
else:
    print(json.dumps({'ok': True}))
""",
                encoding="utf-8",
            )
            fake_opencli.chmod(0o755)
            output = temp / "bundle"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_facebook.py"),
                    "--company",
                    "Example Company",
                    "--page",
                    "https://facebook.com/ExamplePage",
                    "--opencli",
                    str(fake_opencli),
                    "--output",
                    str(output),
                    "--delay",
                    "0",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )

            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(manifest["status"], "human_verification_required")
            self.assertTrue(manifest["challenge_detected"])
            self.assertFalse((output / "report.html").exists())
            self.assertFalse((output / "analysis/analysis_task.md").exists())
            self.assertIn("will not bypass", result.stderr)


if __name__ == "__main__":
    unittest.main()
