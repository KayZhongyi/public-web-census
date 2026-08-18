#!/usr/bin/env python3
"""Collect public YouTube channel metadata into a Public Web Census bundle."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TABS = ("videos", "shorts", "streams")
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
    "duration_seconds",
    "channel_id",
    "availability",
    "source_tab",
    "collected_at",
    "retrieval_status",
]
CENSUS_FIELDS = [
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
COMMENT_FIELDS = [
    "comment_id",
    "content_id",
    "parent_comment_id",
    "commenter",
    "commenter_type",
    "is_official",
    "text_original",
    "text_translation",
    "likes",
    "topic",
    "response_mode",
    "url",
]
PRINT_TEMPLATE = (
    "%(.{id,title,description,upload_date,timestamp,view_count,like_count,"
    "comment_count,duration,language,channel,channel_id,channel_follower_count,"
    "uploader_id,uploader_url,webpage_url,availability})j"
)


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "youtube-census"


def normalize_channel_base(raw_url: str) -> str:
    parts = urlsplit(raw_url.strip())
    host = parts.netloc.lower().split(":", 1)[0]
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        raise ValueError("Channel URL must be on youtube.com")

    segments = [segment for segment in parts.path.split("/") if segment]
    if segments and segments[-1].lower() in ALLOWED_TABS:
        segments.pop()
    if not segments:
        raise ValueError("Provide a YouTube channel URL, not the YouTube home page")

    valid = segments[0].startswith("@") or segments[0].lower() in {"channel", "c", "user"}
    if not valid:
        raise ValueError("Expected a channel URL such as https://www.youtube.com/@handle")
    expected_length = 1 if segments[0].startswith("@") else 2
    if len(segments) != expected_length:
        raise ValueError("Channel URL must point to the channel root or a supported content tab")

    path = "/" + "/".join(segments)
    return urlunsplit(("https", "www.youtube.com", path, "", ""))


def parse_tabs(value: str) -> list[str]:
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    invalid = sorted(set(requested) - set(ALLOWED_TABS))
    if invalid:
        raise ValueError(f"Unsupported tab(s): {', '.join(invalid)}")
    return list(dict.fromkeys(requested)) or list(ALLOWED_TABS)


def parse_since(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--since must use YYYY-MM-DD") from exc
    return parsed.strftime("%Y%m%d")


def resolve_ytdlp(explicit_path: str | None) -> list[str]:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"yt-dlp executable not found: {path}")
        return [str(path)]

    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]

    try:
        __import__("yt_dlp")
    except ImportError as exc:
        raise FileNotFoundError(
            'yt-dlp is required for live collection. Install it with: '
            'python3 -m pip install -U "yt-dlp[default]"'
        ) from exc
    return [sys.executable, "-m", "yt_dlp"]


def ytdlp_version(command: list[str]) -> str:
    result = subprocess.run(
        [*command, "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def collect_tab(
    command: list[str],
    tab_url: str,
    max_items: int,
    sleep_requests: float,
    date_after: str | None,
) -> tuple[list[dict[str, object]], str]:
    args = [
        *command,
        "--skip-download",
        "--ignore-errors",
        "--no-warnings",
        "--no-progress",
        "--retries",
        "3",
        "--sleep-requests",
        str(sleep_requests),
        "--print",
        PRINT_TEMPLATE,
    ]
    if max_items > 0:
        args.extend(["--playlist-end", str(max_items)])
    if date_after:
        args.extend(["--dateafter", date_after])
    args.append(tab_url)

    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"yt-dlp returned invalid JSON on output line {line_number}") from exc
        if record.get("id"):
            records.append(record)

    stderr_tail = "\n".join(result.stderr.splitlines()[-12:])
    if result.returncode and not records:
        detail = stderr_tail or f"yt-dlp exited with status {result.returncode}"
        raise RuntimeError(f"Unable to collect {tab_url}: {detail}")
    return records, stderr_tail


def record_score(record: dict[str, object]) -> int:
    useful = (
        "title",
        "description",
        "upload_date",
        "timestamp",
        "view_count",
        "like_count",
        "comment_count",
        "duration",
        "language",
    )
    return sum(record.get(field) not in (None, "") for field in useful)


def merge_records(tab_records: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    tabs_by_id: dict[str, list[str]] = {}
    for tab, records in tab_records.items():
        for record in records:
            record_id = clean_text(record.get("id"))
            tabs_by_id.setdefault(record_id, []).append(tab)
            if record_id not in merged or record_score(record) > record_score(merged[record_id]):
                merged[record_id] = record
    for record_id, record in merged.items():
        record["_source_tabs"] = ",".join(dict.fromkeys(tabs_by_id[record_id]))
    return list(merged.values())


def published_at(record: dict[str, object]) -> str:
    timestamp = record.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    raw_date = clean_text(record.get("upload_date"))
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    return ""


def as_csv_number(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(float(str(value))))
    except ValueError:
        return ""


def to_content_rows(
    records: list[dict[str, object]], company: str, collected_at: str
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        title = clean_text(record.get("title")).strip()
        description = clean_text(record.get("description")).strip()
        text_original = title if not description else f"{title}\n\n{description}"
        video_id = clean_text(record.get("id"))
        rows.append(
            {
                "record_id": f"YT-{video_id}",
                "platform": "YouTube",
                "account": clean_text(record.get("uploader_id") or record.get("channel")),
                "published_at": published_at(record),
                "language": clean_text(record.get("language")),
                "text_original": text_original,
                "text_translation": "",
                "views": as_csv_number(record.get("view_count")),
                "likes": as_csv_number(record.get("like_count")),
                "comments_count": as_csv_number(record.get("comment_count")),
                "shares": "",
                "url": clean_text(
                    record.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
                ),
                "content_type": "unclassified",
                "brand": company,
                "duration_seconds": as_csv_number(record.get("duration")),
                "channel_id": clean_text(record.get("channel_id")),
                "availability": clean_text(record.get("availability")),
                "source_tab": clean_text(record.get("_source_tabs")),
                "collected_at": collected_at,
                "retrieval_status": "captured",
            }
        )
    return sorted(rows, key=lambda row: (row["published_at"], row["record_id"]), reverse=True)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_census_row(
    channel_base: str,
    rows: list[dict[str, str]],
    records: list[dict[str, object]],
    selected_tabs: list[str],
    limited: bool,
) -> dict[str, str]:
    first = records[0] if records else {}
    handle = clean_text(first.get("uploader_id") or first.get("channel"))
    channel_id = clean_text(first.get("channel_id"))
    evidence = "; ".join(part for part in (handle, channel_id) if part)
    note_prefix = "Limited test run" if limited else "Best-effort selected-tab census"
    return {
        "platform": "YouTube",
        "handle": handle,
        "url": channel_base,
        "identity_status": "unverified",
        "identity_evidence": f"Automated metadata only: {evidence}".rstrip(": "),
        "followers": as_csv_number(first.get("channel_follower_count")),
        "visible_items": str(len(rows)),
        "last_active_at": rows[0]["published_at"] if rows else "",
        "deep_dive": "yes",
        "notes": (
            f"{note_prefix}; captured {len(rows)} unique public items from "
            f"{', '.join(selected_tabs)}. Verify account identity before analysis."
        ),
    }


def write_bundle(
    output_dir: Path,
    company: str,
    channel_input: str,
    channel_base: str,
    selected_tabs: list[str],
    max_items: int,
    since: str | None,
    records_by_tab: dict[str, list[dict[str, object]]],
    warnings_by_tab: dict[str, str],
    version: str,
    request_delay_seconds: float,
) -> tuple[int, Path]:
    collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records = merge_records(records_by_tab)
    if not records:
        raise RuntimeError("No public video records were returned for the selected tabs")
    rows = to_content_rows(records, company, collected_at)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_csv(output_dir / "content.csv", CONTENT_FIELDS, rows)
    write_csv(output_dir / "comments.csv", COMMENT_FIELDS, [])
    write_csv(
        output_dir / "platform_census.csv",
        CENSUS_FIELDS,
        [
            build_census_row(
                channel_base,
                rows,
                records,
                selected_tabs,
                max_items > 0,
            )
        ],
    )

    manifest = {
        "schema_version": "0.2",
        "target": {"company": company, "platform": "YouTube"},
        "input_url": channel_input,
        "normalized_channel_url": channel_base,
        "selected_tabs": selected_tabs,
        "max_items_per_tab": max_items,
        "since": since,
        "cutoff_utc": collected_at,
        "collector": {"name": "yt-dlp", "version": version},
        "source_access": "public unauthenticated metadata; no media downloaded",
        "collection_controls": {
            "request_delay_seconds": request_delay_seconds,
            "retry_limit": 3,
            "authentication": "not used by this adapter",
            "captcha_policy": "stop and require a human; never bypass",
            "media_download": "disabled",
        },
        "scope": (
            ("Limited test run" if max_items > 0 else "Best-effort all retrievable entries in selected tabs")
            + (f" published on or after {since}" if since else "")
        ),
        "counts": {
            "unique_content": len(rows),
            "by_tab_before_deduplication": {
                tab: len(tab_rows) for tab, tab_rows in records_by_tab.items()
            },
            "comments": 0,
        },
        "field_notes": {
            "publication_date": "Captured from individual public video metadata when available",
            "views_likes_comments": "Point-in-time visible values; fields may be blank",
            "shares": "Not exposed by this adapter",
            "translation_and_classification": "Left blank/unclassified for the Agent analysis phase",
        },
        "warnings": {tab: warning for tab, warning in warnings_by_tab.items() if warning},
        "limitations": [
            "Account identity is not automatically verified.",
            "Deleted, private, members-only, region-restricted, age-restricted, and personalized items may be absent.",
            "YouTube and yt-dlp behavior can change; rerun with a current yt-dlp release.",
            "Baseline collection does not include comments. Run ./public-web-census youtube-comments for selected public videos.",
        ],
    }
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(rows), manifest_path


def build_report(output_dir: Path, company: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_demo.py"),
            "--census",
            str(output_dir / "platform_census.csv"),
            "--content",
            str(output_dir / "content.csv"),
            "--comments",
            str(output_dir / "comments.csv"),
            "--output",
            str(output_dir / "report.html"),
            "--json",
            str(output_dir / "summary.json"),
            "--dataset-label",
            f"{company} YouTube census",
            "--dataset-kind",
            "live",
            "--quiet",
        ],
        check=True,
    )


def prepare_analysis_packet(output_dir: Path) -> Path:
    task_path = output_dir / "analysis" / "analysis_task.md"
    if task_path.exists():
        return task_path
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_analysis.py"),
            "--bundle",
            str(output_dir),
        ],
        check=True,
    )
    return task_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, help="Company or target label used in output")
    parser.add_argument("--channel", required=True, help="Public YouTube channel URL")
    parser.add_argument(
        "--tabs",
        default=",".join(ALLOWED_TABS),
        help="Comma-separated tabs: videos,shorts,streams (default: all three)",
    )
    parser.add_argument(
        "--max-items-per-tab",
        type=int,
        default=0,
        help="Limit each selected tab for a trial run; 0 means all retrievable items",
    )
    parser.add_argument(
        "--since",
        help="Only request records published on or after YYYY-MM-DD",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: runs/<company-slug>)",
    )
    parser.add_argument("--yt-dlp", dest="ytdlp_path", help="Path to a yt-dlp executable")
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=0.75,
        help="Delay between metadata requests in seconds (default: 0.75)",
    )
    parser.add_argument("--no-report", action="store_true", help="Write evidence bundle only")
    parser.add_argument(
        "--no-analysis-packet",
        action="store_true",
        help="Skip the model-agnostic Agent analysis handoff files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_items_per_tab < 0:
        raise SystemExit("--max-items-per-tab must be 0 or greater")
    if args.sleep_requests < 0:
        raise SystemExit("--sleep-requests must be 0 or greater")

    try:
        channel_base = normalize_channel_base(args.channel)
        selected_tabs = parse_tabs(args.tabs)
        date_after = parse_since(args.since)
        command = resolve_ytdlp(args.ytdlp_path)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = args.output or ROOT / "runs" / safe_slug(args.company)
    version = ytdlp_version(command)
    records_by_tab: dict[str, list[dict[str, object]]] = {}
    warnings_by_tab: dict[str, str] = {}

    print(f"Target: {args.company}")
    print(f"Channel: {channel_base}")
    print(f"yt-dlp: {version}")
    for tab in selected_tabs:
        tab_url = f"{channel_base}/{tab}"
        print(f"Collecting {tab_url} ...", flush=True)
        records, warning = collect_tab(
            command,
            tab_url,
            args.max_items_per_tab,
            args.sleep_requests,
            date_after,
        )
        records_by_tab[tab] = records
        warnings_by_tab[tab] = warning
        print(f"  {len(records)} records")

    row_count, manifest_path = write_bundle(
        output_dir=output_dir,
        company=args.company,
        channel_input=args.channel,
        channel_base=channel_base,
        selected_tabs=selected_tabs,
        max_items=args.max_items_per_tab,
        since=args.since,
        records_by_tab=records_by_tab,
        warnings_by_tab=warnings_by_tab,
        version=version,
        request_delay_seconds=args.sleep_requests,
    )
    if not args.no_report:
        build_report(output_dir, args.company)
    analysis_task = None
    if not args.no_analysis_packet:
        analysis_task = prepare_analysis_packet(output_dir)

    print(f"Captured {row_count} unique public video records.")
    print(f"Evidence bundle: {output_dir}")
    print(f"Manifest: {manifest_path}")
    if not args.no_report:
        print(f"Report: {output_dir / 'report.html'}")
    if analysis_task:
        print(f"Next Agent task: {analysis_task}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
