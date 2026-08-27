#!/usr/bin/env python3
"""Convert the original Chinese TikTok CSV export into a standard evidence bundle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


CONTENT_FIELDS = [
    "record_id",
    "platform",
    "account",
    "published_at",
    "language",
    "text_original",
    "text_translation",
    "views",
    "likes",
    "comments_count",
    "shares",
    "url",
    "content_type",
    "brand",
    "collected_at",
    "retrieval_status",
]
COMMENT_FIELDS = [
    "comment_id",
    "content_id",
    "parent_comment_id",
    "commenter",
    "commenter_type",
    "is_official",
    "text_original",
    "text_translation",
    "published_at",
    "likes",
    "topic",
    "response_mode",
    "url",
]
PLATFORM_FIELDS = [
    "platform",
    "handle",
    "url",
    "identity_status",
    "identity_evidence",
    "followers",
    "visible_items",
    "last_active_at",
    "deep_dive",
    "notes",
]


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{clean(k): clean(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def account_from_url(url: str) -> str:
    match = re.search(r"/(@[^/]+)/video/", url)
    return match.group(1).lstrip("@") if match else ""


def comment_id(content_id: str, user: str, text: str) -> str:
    digest = hashlib.sha256("\n".join((content_id, "", user, text)).encode("utf-8")).hexdigest()[:20]
    return f"TTC-{digest}"


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return value


def legacy_bundle(source: Path, output: Path, observed_at: str) -> dict[str, int]:
    videos = read_csv(source / "videos.csv")
    comments = read_csv(source / "comments.csv")
    census = read_csv(source / "platform_census.csv")
    if not videos:
        raise ValueError(f"No rows found in {source / 'videos.csv'}")

    first_url = next((row.get("链接", "") for row in videos if row.get("链接")), "")
    account = account_from_url(first_url)
    if not account:
        raise ValueError("Cannot derive TikTok account from videos.csv links")
    normalized_url = f"https://www.tiktok.com/@{account}"
    captured_at = observed_at

    content_rows = []
    for row in videos:
        url = row.get("链接", "")
        record_id = row.get("vid", "")
        if not record_id or not url:
            continue
        content_rows.append(
            {
                "record_id": f"TT-{record_id}",
                "platform": "TikTok",
                "account": account,
                "published_at": parse_date(row.get("发布日期", "")),
                "language": "my",
                "text_original": row.get("原文缅文", ""),
                "text_translation": row.get("译文中文", ""),
                "views": row.get("播放", ""),
                "likes": row.get("点赞", ""),
                "comments_count": row.get("评论数", ""),
                "shares": row.get("分享", ""),
                "url": url.split("?", 1)[0].rstrip("/"),
                "content_type": row.get("内容类型", ""),
                "brand": row.get("主推品牌", ""),
                "collected_at": captured_at,
                "retrieval_status": "legacy_imported",
            }
        )

    comment_rows = []
    seen_ids: set[str] = set()
    for row in comments:
        content_id = row.get("所属vid", "")
        text = row.get("评论原文", "")
        user = row.get("用户名", "")
        if not content_id or not text:
            continue
        normalized_content_id = f"TT-{content_id}"
        cid = comment_id(normalized_content_id, user, text)
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        official = row.get("是否官方", "").lower() in {"是", "true", "1", "yes"}
        comment_rows.append(
            {
                "comment_id": cid,
                "content_id": normalized_content_id,
                "parent_comment_id": "",
                "commenter": user,
                "commenter_type": "official" if official else "unclassified",
                "is_official": "true" if official else "false",
                "text_original": text,
                "text_translation": row.get("译文中文", ""),
                "published_at": "",
                "likes": row.get("点赞数", ""),
                "topic": "",
                "response_mode": "",
                "url": row.get("视频链接", "").split("?", 1)[0].rstrip("/"),
            }
        )

    census_row = next(
        (row for row in census if row.get("平台", "").casefold() == "tiktok"), {}
    )
    platform_row = {
        "platform": "TikTok",
        "handle": (census_row.get("handle", "") or account).lstrip("@"),
        "url": census_row.get("链接", "") or normalized_url,
        "identity_status": "verified",
        "identity_evidence": "legacy account census and canonical video links",
        "followers": census_row.get("粉丝", ""),
        "visible_items": str(len(content_rows)),
        "last_active_at": max((row["published_at"] for row in content_rows if row["published_at"]), default=""),
        "deep_dive": "yes",
        "notes": "Imported from the original Chinese CSV export; source rows retained outside this bundle.",
    }

    manifest = {
        "schema_version": "1.0",
        "research_mode": "competitor_intelligence",
        "target": {"company": "MK Solar Myanmar", "platform": "TikTok", "account": account},
        "input_url": census_row.get("链接", "") or normalized_url,
        "normalized_profile_url": normalized_url,
        "cutoff_utc": captured_at,
        "source_access": "legacy CSV export collected through the authorized browser workflow",
        "status": "completed_legacy_import",
        "counts": {"unique_content": len(content_rows), "comments": len(comment_rows)},
        "legacy_source": str(source),
        "limitations": [
            "Legacy export did not contain stable comment IDs or reply-parent IDs; IDs are reconstructed from content, user, and text.",
            "Duplicate legacy comment rows are collapsed by the reconstructed stable ID.",
            "This import is a historical baseline, not a fresh platform capture.",
        ],
    }
    write_csv(output / "content.csv", CONTENT_FIELDS, sorted(content_rows, key=lambda row: row["record_id"]))
    write_csv(output / "comments.csv", COMMENT_FIELDS, sorted(comment_rows, key=lambda row: row["comment_id"]))
    write_csv(output / "platform_census.csv", PLATFORM_FIELDS, [platform_row])
    output.mkdir(parents=True, exist_ok=True)
    (output / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"content": len(content_rows), "comments": len(comment_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the original MK TikTok CSV export")
    parser.add_argument("--source", required=True, type=Path, help="Directory containing videos.csv, comments.csv, platform_census.csv")
    parser.add_argument("--output", required=True, type=Path, help="Standard evidence bundle directory")
    parser.add_argument("--observed-at", default="2026-07-06T00:00:00+00:00", help="Historical cutoff in ISO-8601")
    args = parser.parse_args()
    counts = legacy_bundle(args.source.resolve(), args.output.resolve(), args.observed_at)
    print(f"Migrated {counts['content']} videos and {counts['comments']} unique comments to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
