from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class LegacyTikTokImportTest(unittest.TestCase):
    def test_import_maps_and_deduplicates_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "bundle"
            source.mkdir()
            write_csv(
                source / "videos.csv",
                ["vid", "平台", "发布日期", "内容类型", "主推品牌", "播放", "点赞", "评论数", "分享", "原文缅文", "译文中文", "链接"],
                [{"vid": "123", "平台": "TikTok", "发布日期": "2026-06-01", "内容类型": "科普", "主推品牌": "", "播放": "10", "点赞": "2", "评论数": "1", "分享": "0", "原文缅文": "မ", "译文中文": "中", "链接": "https://www.tiktok.com/@mksolarmyanmar/video/123"}],
            )
            write_csv(
                source / "comments.csv",
                ["所属vid", "视频播放", "视频标题", "类型", "用户名", "是否官方", "点赞数", "评论原文", "译文中文", "视频链接"],
                [
                    {"所属vid": "123", "视频播放": "10", "视频标题": "", "类型": "评论", "用户名": "u", "是否官方": "", "点赞数": "1", "评论原文": "问", "译文中文": "问", "视频链接": "https://www.tiktok.com/@mksolarmyanmar/video/123"},
                    {"所属vid": "123", "视频播放": "10", "视频标题": "", "类型": "评论", "用户名": "u", "是否官方": "", "点赞数": "1", "评论原文": "问", "译文中文": "问", "视频链接": "https://www.tiktok.com/@mksolarmyanmar/video/123"},
                ],
            )
            write_csv(
                source / "platform_census.csv",
                ["平台", "handle", "粉丝", "是否活跃", "是否深挖", "链接", "备注"],
                [{"平台": "TikTok", "handle": "@mksolarmyanmar", "粉丝": "7391", "是否活跃": "是", "是否深挖": "是", "链接": "https://www.tiktok.com/@mksolarmyanmar", "备注": ""}],
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/migrate_legacy_tiktok.py"), "--source", str(source), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("1 videos and 1 unique comments", result.stdout)
            with (output / "content.csv").open(encoding="utf-8", newline="") as handle:
                content = list(csv.DictReader(handle))
            with (output / "comments.csv").open(encoding="utf-8", newline="") as handle:
                comments = list(csv.DictReader(handle))
            self.assertEqual(content[0]["record_id"], "TT-123")
            self.assertEqual(content[0]["account"], "mksolarmyanmar")
            self.assertEqual(len(comments), 1)
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed_legacy_import")


if __name__ == "__main__":
    unittest.main()
