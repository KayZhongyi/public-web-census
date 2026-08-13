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

import collect_tiktok_comments  # noqa: E402


CONTENT_FIELDS = [
    "record_id", "platform", "account", "published_at", "language", "text_original",
    "text_translation", "views", "likes", "comments_count", "shares", "url",
    "content_type", "brand", "published_label", "metric_labels", "collected_at",
    "retrieval_status",
]


class TikTokCommentsCollectorTest(unittest.TestCase):
    def write_content(self, bundle: Path) -> None:
        bundle.mkdir(parents=True)
        with (bundle / "content.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CONTENT_FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "record_id": "TT-7416929728608701704",
                    "platform": "TikTok",
                    "account": "example",
                    "published_at": "2024-09-21T00:00:00+00:00",
                    "language": "en",
                    "text_original": "Example video",
                    "text_translation": "",
                    "views": "1200",
                    "likes": "",
                    "comments_count": "",
                    "shares": "",
                    "url": "https://www.tiktok.com/@example/video/7416929728608701704",
                    "content_type": "unclassified",
                    "brand": "Example",
                    "published_label": "",
                    "metric_labels": "",
                    "collected_at": "2026-08-13T00:00:00+00:00",
                    "retrieval_status": "captured",
                }
            )

    def test_identity_and_stable_comment_ids(self) -> None:
        content = {
            "record_id": "TT-1",
            "url": "https://www.tiktok.com/@example/video/1",
        }
        rows = collect_tiktok_comments.raw_rows_to_comments(
            [
                {"level": 1, "user": "viewer", "userHref": "/@viewer", "text": "How much?", "likeLabel": "3", "creator": False},
                {"level": 2, "user": "Example", "userHref": "/@example", "text": "Please call us", "likeLabel": "1", "creator": True, "parentUser": "viewer", "parentText": "How much?"},
            ],
            content,
            {"example"},
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["parent_comment_id"], rows[0]["comment_id"])
        self.assertEqual(rows[1]["is_official"], "true")
        self.assertEqual(rows[0]["likes"], "3")
        self.assertEqual(
            rows[0]["comment_id"],
            collect_tiktok_comments.raw_rows_to_comments(
                [{"level": 1, "user": "viewer", "userHref": "/@viewer", "text": "How much?", "likeLabel": "3", "creator": False}],
                content,
                {"example"},
            )[0]["comment_id"],
        )

    def test_cli_collects_comments_updates_metrics_and_prepares_voice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            bundle = temp / "bundle"
            self.write_content(bundle)
            fake_opencli = temp / "opencli"
            fake_opencli.write_text(
                """#!/usr/bin/env python3
import json
import sys
args = sys.argv[1:]
if args == ['--version']:
    print('opencli 9.9.9-test')
    raise SystemExit(0)
command = args[2]
if command in {'open', 'wait', 'close', 'scroll', 'click'}:
    print(json.dumps({'ok': True}))
elif command == 'find':
    print(json.dumps({'matches_n': 0, 'entries': []}))
elif command == 'eval':
    print(json.dumps({
        'url': 'https://www.tiktok.com/@example/video/7416929728608701704',
        'title': 'Example | TikTok',
        'challenge': False,
        'commentPanel': True,
        'replyExpanders': 0,
        'metrics': {'likes': '45', 'comments': '2', 'shares': '6'},
        'rows': [
            {'level': 1, 'user': 'viewer', 'userHref': '/@viewer', 'text': 'How much?', 'likeLabel': '3', 'creator': False},
            {'level': 2, 'user': 'Example', 'userHref': '/@example', 'text': 'Please call us', 'likeLabel': '1', 'creator': True},
        ],
    }))
else:
    raise SystemExit(2)
""",
                encoding="utf-8",
            )
            fake_opencli.chmod(0o755)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_tiktok_comments.py"),
                    "--bundle",
                    str(bundle),
                    "--top",
                    "1",
                    "--opencli",
                    str(fake_opencli),
                    "--max-scrolls-per-video",
                    "0",
                    "--delay",
                    "0",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            with (bundle / "comments.csv").open(encoding="utf-8", newline="") as handle:
                comments = list(csv.DictReader(handle))
            with (bundle / "content.csv").open(encoding="utf-8", newline="") as handle:
                content = list(csv.DictReader(handle))
            manifest = json.loads((bundle / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(comments), 2)
            self.assertEqual(sum(row["is_official"] == "true" for row in comments), 1)
            self.assertEqual(content[0]["likes"], "45")
            self.assertEqual(content[0]["comments_count"], "2")
            self.assertEqual(content[0]["shares"], "6")
            self.assertEqual(manifest["conversation_collection"]["status"], "completed")
            self.assertTrue((bundle / "voice" / "voice_task.md").exists())
            self.assertIn("Captured 2 unique public comments/replies", result.stdout)

    def test_challenge_stops_with_a_resumable_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            bundle = temp / "bundle"
            self.write_content(bundle)
            fake_opencli = temp / "opencli"
            fake_opencli.write_text(
                """#!/usr/bin/env python3
import json
import sys
args = sys.argv[1:]
if args[2] in {'open', 'close', 'wait'}:
    print(json.dumps({'ok': True}))
elif args[2] == 'eval':
    print(json.dumps({'url': 'https://www.tiktok.com/captcha/', 'title': 'Verify', 'challenge': True, 'commentPanel': False, 'rows': []}))
else:
    print(json.dumps({'ok': True}))
""",
                encoding="utf-8",
            )
            fake_opencli.chmod(0o755)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_tiktok_comments.py"),
                    "--bundle",
                    str(bundle),
                    "--top",
                    "1",
                    "--opencli",
                    str(fake_opencli),
                    "--delay",
                    "0",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            manifest = json.loads((bundle / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result.returncode, 2)
            self.assertTrue((bundle / "comments.csv").exists())
            self.assertEqual(manifest["conversation_collection"]["status"], "human_verification_required")
            self.assertTrue(manifest["conversation_collection"]["challenge_detected"])


if __name__ == "__main__":
    unittest.main()
