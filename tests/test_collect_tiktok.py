from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_tiktok  # noqa: E402


class TikTokCollectorTest(unittest.TestCase):
    def test_profile_url_numbers_timestamp_and_caption(self) -> None:
        self.assertEqual(
            collect_tiktok.normalize_profile_url("@Example.Company"),
            "https://www.tiktok.com/@Example.Company",
        )
        self.assertEqual(
            collect_tiktok.normalize_profile_url("https://m.tiktok.com/@Example.Company?lang=en"),
            "https://www.tiktok.com/@Example.Company",
        )
        with self.assertRaises(ValueError):
            collect_tiktok.normalize_profile_url("https://example.com/@example")

        video_id = "7416929728608701704"
        expected = datetime.fromtimestamp(int(video_id) >> 32, timezone.utc).replace(microsecond=0).isoformat()
        self.assertEqual(collect_tiktok.published_at_from_video_id(video_id), expected)
        self.assertEqual(collect_tiktok.parse_compact_number("23.2K"), "23200")
        self.assertEqual(collect_tiktok.parse_compact_number("1.3万"), "13000")
        self.assertEqual(
            collect_tiktok.caption_from_accessibility_text(
                "Example 使用 Example 的 原声 - Example 创作的 Public caption #tag",
                "Example",
                "example",
            ),
            "Public caption #tag",
        )

    def test_cli_accumulates_profile_windows_and_writes_bundle(self) -> None:
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
    raise SystemExit(2)
round_index = int(state_file.read_text() if state_file.exists() else '0')
items = [{
    'url': 'https://www.tiktok.com/@example/video/7416929728608701704?lang=en',
    'accessibilityText': 'Example 使用 Example 的 original sound - Example 创作的 First public caption',
    'viewsLabel': '1.2K',
    'badge': 'Pinned',
}]
if round_index >= 1:
    items.append({
        'url': 'https://www.tiktok.com/@example/video/7515266782635035922',
        'accessibilityText': 'Example created by Second public caption',
        'viewsLabel': '80',
        'badge': '',
    })
print(json.dumps({
    'url': 'https://www.tiktok.com/@example',
    'title': 'Example (@example) | TikTok',
    'displayName': 'Example',
    'handle': 'example',
    'followersLabel': '7.6K',
    'likesLabel': '35K',
    'scrollY': round_index * 2400,
    'scrollHeight': 5000 + round_index * 1000,
    'challenge': False,
    'items': items,
}))
""",
                encoding="utf-8",
            )
            fake_opencli.chmod(0o755)
            output = temp / "bundle"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_tiktok.py"),
                    "--company",
                    "Example Company",
                    "--profile",
                    "@example",
                    "--opencli",
                    str(fake_opencli),
                    "--output",
                    str(output),
                    "--max-scrolls",
                    "1",
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
            with (output / "platform_census.csv").open(encoding="utf-8", newline="") as handle:
                census = list(csv.DictReader(handle))

            self.assertEqual({row["record_id"] for row in rows}, {"TT-7416929728608701704", "TT-7515266782635035922"})
            first = next(row for row in rows if row["record_id"] == "TT-7416929728608701704")
            self.assertEqual(first["views"], "1200")
            self.assertEqual(first["text_original"], "First public caption")
            self.assertTrue(first["published_at"].startswith("2024-09-"))
            self.assertEqual(first["url"], "https://www.tiktok.com/@example/video/7416929728608701704")
            self.assertEqual(census[0]["followers"], "7600")
            self.assertEqual(manifest["collector"]["strategy"], "visible DOM state")
            self.assertEqual(manifest["collection_controls"]["write_actions"], "none")
            self.assertEqual(manifest["counts"]["unique_content"], 2)
            self.assertIn("Captured 2 unique public TikTok video records", result.stdout)
            self.assertTrue((output / "analysis" / "analysis_task.md").exists())

    def test_human_verification_writes_checkpoint_and_stops(self) -> None:
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
    print(json.dumps({'url': 'https://www.tiktok.com/captcha/', 'title': 'Verify', 'challenge': True, 'items': []}))
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
                    str(ROOT / "scripts/collect_tiktok.py"),
                    "--company",
                    "Example Company",
                    "--profile",
                    "@example",
                    "--opencli",
                    str(fake_opencli),
                    "--output",
                    str(output),
                    "--max-scrolls",
                    "0",
                    "--delay",
                    "0",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(manifest["status"], "human_verification_required")
            self.assertTrue(manifest["challenge_detected"])

    def test_project_cli_exposes_tiktok_commands(self) -> None:
        for command, expected in (("tiktok", "--profile"), ("tiktok-comments", "--bundle")):
            result = subprocess.run(
                [sys.executable, str(ROOT / "competitor-census"), command, "--help"],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
