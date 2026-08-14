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

import collect_youtube  # noqa: E402
import collect_youtube_comments  # noqa: E402


class YouTubeCollectorTest(unittest.TestCase):
    def test_normalizes_supported_channel_urls(self) -> None:
        self.assertEqual(
            collect_youtube.normalize_channel_base("https://m.youtube.com/@Example/videos?view=0"),
            "https://www.youtube.com/@Example",
        )
        self.assertEqual(
            collect_youtube.normalize_channel_base("https://youtube.com/channel/UC123/shorts"),
            "https://www.youtube.com/channel/UC123",
        )
        with self.assertRaises(ValueError):
            collect_youtube.normalize_channel_base("https://www.youtube.com/watch?v=abc")
        with self.assertRaises(ValueError):
            collect_youtube.normalize_channel_base("https://www.youtube.com/@Example/featured")
        self.assertEqual(collect_youtube.parse_since("2026-01-02"), "20260102")
        with self.assertRaises(ValueError):
            collect_youtube.parse_since("02/01/2026")

    def test_project_cli_exposes_youtube_comment_command(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "competitor-census"), "--help"],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertIn("youtube-comments", result.stdout)

    def test_live_cli_writes_a_deduplicated_bundle_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake_ytdlp = temp / "yt-dlp"
            fake_ytdlp.write_text(
                """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("2099.01.01-test")
    raise SystemExit(0)

tab = sys.argv[-1].rstrip("/").split("/")[-1]
video_id = "video-1" if tab == "videos" else "short-1"
record = {
    "id": video_id,
    "title": "Public title " + tab,
    "description": "Public description",
    "upload_date": "20260102",
    "timestamp": 1767312000,
    "view_count": 1200 if tab == "videos" else 800,
    "like_count": 80,
    "comment_count": 9,
    "duration": 60,
    "language": "en",
    "channel": "Example Company",
    "channel_id": "UC-EXAMPLE",
    "channel_follower_count": 5000,
    "uploader_id": "@ExampleCompany",
    "uploader_url": "https://www.youtube.com/@ExampleCompany",
    "webpage_url": "https://www.youtube.com/watch?v=" + video_id,
    "availability": "public",
}
print(json.dumps(record))
""",
                encoding="utf-8",
            )
            fake_ytdlp.chmod(0o755)
            output = temp / "bundle"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_youtube.py"),
                    "--company",
                    "Example Company",
                    "--channel",
                    "https://www.youtube.com/@ExampleCompany",
                    "--tabs",
                    "videos,shorts",
                    "--max-items-per-tab",
                    "1",
                    "--since",
                    "2026-01-01",
                    "--yt-dlp",
                    str(fake_ytdlp),
                    "--output",
                    str(output),
                    "--sleep-requests",
                    "1.25",
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

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["content_type"], "unclassified")
            self.assertEqual(rows[0]["text_translation"], "")
            self.assertEqual(manifest["collector"]["version"], "2099.01.01-test")
            self.assertEqual(manifest["since"], "2026-01-01")
            self.assertEqual(manifest["counts"]["unique_content"], 2)
            self.assertEqual(manifest["collection_controls"]["request_delay_seconds"], 1.25)
            self.assertEqual(manifest["collection_controls"]["retry_limit"], 3)
            self.assertEqual(manifest["collection_controls"]["captcha_policy"], "stop and require a human; never bypass")
            self.assertIn("Comments not included", report)
            self.assertIn("content classification are intentionally pending", report)
            self.assertTrue((output / "analysis/analysis_task.md").exists())
            self.assertTrue((output / "analysis/analysis_results.csv").exists())
            self.assertIn("Captured 2 unique public video records", result.stdout)
            self.assertIn("Next Agent task", result.stdout)

    def test_comment_cli_writes_traceable_replies_without_an_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            fake_ytdlp = temp / "yt-dlp"
            fake_ytdlp.write_text(
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("2099.01.01-test")
    raise SystemExit(0)

template = sys.argv[sys.argv.index("-o") + 1]
path = Path(template.replace("%(id)s", "video-1").replace("%(ext)s", "info.json"))
path.write_text(json.dumps({
    "id": "video-1",
    "comments": [
        {
            "id": "parent-1",
            "parent": "root",
            "author": "Customer One",
            "author_id": "UC-CUSTOMER",
            "text": "How much does this system cost?",
            "like_count": 4,
            "timestamp": 1767312000,
        },
        {
            "id": "reply-1",
            "parent": "parent-1",
            "author": "Example Company",
            "author_id": "UC-EXAMPLE",
            "author_is_uploader": True,
            "text": "Please contact our local dealer.",
            "like_count": 1,
            "timestamp": 1767315600,
        },
    ],
}), encoding="utf-8")
""",
                encoding="utf-8",
            )
            fake_ytdlp.chmod(0o755)
            bundle = temp / "bundle"
            bundle.mkdir()
            content = {
                "record_id": "YT-video-1",
                "platform": "YouTube",
                "account": "@ExampleCompany",
                "published_at": "2026-01-02T00:00:00+00:00",
                "language": "en",
                "text_original": "A public video",
                "text_translation": "",
                "views": "1200",
                "likes": "80",
                "comments_count": "9",
                "shares": "",
                "url": "https://www.youtube.com/watch?v=video-1",
                "content_type": "unclassified",
                "brand": "Example Company",
                "duration_seconds": "60",
                "channel_id": "UC-EXAMPLE",
                "availability": "public",
                "source_tab": "videos",
                "collected_at": "2026-01-02T00:00:00+00:00",
                "retrieval_status": "captured",
            }
            with (bundle / "content.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=collect_youtube.CONTENT_FIELDS)
                writer.writeheader()
                writer.writerow(content)
            with (bundle / "comments.csv").open("w", encoding="utf-8", newline="") as handle:
                csv.DictWriter(handle, fieldnames=collect_youtube.COMMENT_FIELDS).writeheader()
            (bundle / "run_manifest.json").write_text("{}\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_youtube_comments.py"),
                    "--bundle",
                    str(bundle),
                    "--top",
                    "1",
                    "--max-comments-per-video",
                    "10",
                    "--yt-dlp",
                    str(fake_ytdlp),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )

            with (bundle / "comments.csv").open(encoding="utf-8", newline="") as handle:
                comments = list(csv.DictReader(handle))
            manifest = json.loads((bundle / "run_manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(len(comments), 2)
            self.assertEqual(comments[0]["comment_id"], "YTC-parent-1")
            self.assertEqual(comments[1]["parent_comment_id"], "YTC-parent-1")
            self.assertEqual(comments[1]["is_official"], "true")
            self.assertIn("lc=reply-1", comments[1]["url"])
            self.assertEqual(comments[0]["published_at"], "2026-01-02T00:00:00+00:00")
            self.assertEqual(manifest["counts"]["comments"], 2)
            self.assertEqual(manifest["conversation_collection"]["max_comments_per_video"], 10)
            self.assertIn("no API key", manifest["conversation_collection"]["source_access"])
            self.assertTrue((bundle / "voice" / "voice_task.md").exists())
            self.assertIn("Captured 2 unique public comments/replies", result.stdout)

    def test_youtube_human_verification_marker_is_never_bypassed(self) -> None:
        self.assertTrue(
            collect_youtube_comments.needs_human_verification(
                "Sign in to confirm you're not a bot before continuing"
            )
        )


if __name__ == "__main__":
    unittest.main()
