#!/usr/bin/env python3
"""Collect selected public YouTube video comments into an evidence bundle."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from collect_youtube import clean_text, resolve_ytdlp, ytdlp_version


ROOT = Path(__file__).resolve().parents[1]
BASE_COMMENT_FIELDS = {
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
}
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
HUMAN_VERIFICATION_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you\u2019re not a bot",
    "captcha",
    "verify you are human",
    "verify you're human",
    "human verification",
)


class HumanVerificationRequired(RuntimeError):
    """Raised when YouTube requests a human verification step."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Evidence-bundle directory containing content.csv")
    parser.add_argument("--top", type=int, default=30, help="Select top N captured YouTube videos by views (default: 30)")
    parser.add_argument("--video", action="append", default=[], help="Collect one public YouTube video URL already in content.csv; repeatable")
    parser.add_argument(
        "--max-comments-per-video",
        type=int,
        default=500,
        help="Maximum public comments and replies per selected video; 0 means all retrievable (default: 500)",
    )
    parser.add_argument("--yt-dlp", dest="ytdlp_path", help="Path to a yt-dlp executable")
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=0.75,
        help="Delay between yt-dlp requests in seconds (default: 0.75)",
    )
    parser.add_argument("--resume", action="store_true", help="Merge with an existing comments.csv checkpoint")
    parser.add_argument(
        "--no-voice-packet",
        action="store_true",
        help="Do not create a customer-voice analysis packet after a completed run",
    )
    return parser.parse_args()


def load_csv(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = sorted(required - set(fields))
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(missing)}")
        return fields, list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def as_csv_number(value: object) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except ValueError:
        return ""


def metric_number(value: object) -> int:
    parsed = as_csv_number(value)
    return int(parsed) if parsed else 0


def video_id_from_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().split(":", 1)[0]
    if host in {"youtu.be", "www.youtu.be"}:
        return parts.path.strip("/").split("/", 1)[0]
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return ""
    if parts.path.rstrip("/") == "/watch":
        return next((value for key, value in parse_qsl(parts.query) if key == "v"), "")
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2 and segments[0].lower() in {"shorts", "live", "embed"}:
        return segments[1]
    return ""


def content_video_id(row: dict[str, str]) -> str:
    from_url = video_id_from_url(row.get("url", ""))
    if from_url:
        return from_url
    record_id = row.get("record_id", "")
    return record_id.removeprefix("YT-") if record_id.startswith("YT-") else ""


def select_content(rows: list[dict[str, str]], videos: list[str], top: int) -> list[dict[str, str]]:
    candidates = [
        row
        for row in rows
        if row.get("platform", "").strip().lower() == "youtube" and content_video_id(row)
    ]
    if videos:
        wanted = {video_id_from_url(value) for value in videos}
        if "" in wanted:
            raise ValueError("--video must be a public YouTube video URL")
        selected = [row for row in candidates if content_video_id(row) in wanted]
        found = {content_video_id(row) for row in selected}
        if missing := sorted(wanted - found):
            raise ValueError("Video URLs are not present in content.csv: " + ", ".join(missing))
        return selected
    return sorted(candidates, key=lambda row: metric_number(row.get("views", "")), reverse=True)[:top]


def comment_published_at(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()


def comment_url(content_url: str, comment_id: str) -> str:
    parts = urlsplit(content_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "lc"]
    query.append(("lc", comment_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def needs_human_verification(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in HUMAN_VERIFICATION_MARKERS)


def read_info_file(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def fetch_comments(
    command: list[str],
    content: dict[str, str],
    max_comments: int,
    sleep_requests: float,
) -> tuple[list[dict[str, object]], str]:
    video_id = content_video_id(content)
    if not video_id:
        raise ValueError(f"Could not determine a YouTube video ID for {content.get('record_id', 'record')}")
    with tempfile.TemporaryDirectory(prefix="public-web-census-youtube-comments-") as temporary:
        output_template = str(Path(temporary) / "%(id)s.%(ext)s")
        args = [
            *command,
            "--skip-download",
            "--write-comments",
            "--write-info-json",
            "--no-progress",
            "--retries",
            "3",
            "--sleep-requests",
            str(sleep_requests),
            "-o",
            output_template,
        ]
        if max_comments > 0:
            args.extend(["--extractor-args", f"youtube:max_comments={max_comments}"])
        args.append(content["url"])
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        diagnostic = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if needs_human_verification(diagnostic):
            raise HumanVerificationRequired(diagnostic)
        info = next(
            (
                value
                for path in sorted(Path(temporary).glob("*.info.json"))
                if (value := read_info_file(path)) and value.get("id") == video_id
            ),
            None,
        )
        if info is None:
            detail = "\n".join(diagnostic.splitlines()[-12:])
            raise RuntimeError(detail or f"yt-dlp did not write comment metadata for {video_id}")
        raw_comments = info.get("comments", [])
        if not isinstance(raw_comments, list):
            raw_comments = []
        warning = "\n".join(diagnostic.splitlines()[-12:]) if result.returncode else ""
        return [row for row in raw_comments if isinstance(row, dict)], warning


def comments_from_raw(raw_comments: list[dict[str, object]], content: dict[str, str]) -> list[dict[str, str]]:
    channel_id = clean_text(content.get("channel_id"))
    rows: list[dict[str, str]] = []
    for raw in raw_comments:
        source_id = clean_text(raw.get("id"))
        text = clean_text(raw.get("text"))
        if not source_id or not text:
            continue
        parent_source_id = clean_text(raw.get("parent"))
        parent_id = "" if parent_source_id in {"", "root"} else f"YTC-{parent_source_id}"
        author = clean_text(raw.get("author")) or clean_text(raw.get("author_id"))
        author_id = clean_text(raw.get("author_id"))
        official = bool(raw.get("author_is_uploader")) or bool(channel_id and author_id == channel_id)
        rows.append(
            {
                "comment_id": f"YTC-{source_id}",
                "content_id": content["record_id"],
                "parent_comment_id": parent_id,
                "commenter": author,
                "commenter_type": "official" if official else "unclassified",
                "is_official": "true" if official else "false",
                "text_original": text,
                "text_translation": "",
                "published_at": comment_published_at(raw.get("timestamp")),
                "likes": as_csv_number(raw.get("like_count")),
                "topic": "",
                "response_mode": "",
                "url": comment_url(content["url"], source_id),
            }
        )
    return rows


def load_checkpoint(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    _fields, rows = load_csv(path, BASE_COMMENT_FIELDS)
    return {row["comment_id"]: row for row in rows if row.get("comment_id")}


def update_manifest(
    bundle: Path,
    version: str,
    selected: int,
    comments: int,
    max_comments: int,
    status: str,
    warnings: list[str],
) -> None:
    path = bundle / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": "0.2"}
    manifest["conversation_collection"] = {
        "collector": "yt-dlp YouTube public-comment extractor",
        "version": version,
        "selected_content": selected,
        "public_comments_and_replies": comments,
        "status": status,
        "selection_rule": "highest captured view count or explicit video URLs",
        "max_comments_per_video": max_comments if max_comments else "all retrievable",
        "cutoff_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_access": "public YouTube pages through yt-dlp; no API key, login, or media download",
        "captcha_policy": "stop and require a human; never bypass",
        "write_actions": "none",
    }
    counts = manifest.setdefault("counts", {})
    counts["comments"] = comments
    field_notes = manifest.setdefault("field_notes", {})
    field_notes["youtube_comments"] = (
        "Public comment ID, parent relationship, display author, available timestamp, like count, "
        "official-uploader marker, and source URL when yt-dlp exposes them"
    )
    limitations = manifest.setdefault("limitations", [])
    obsolete = "Comments and replies are not collected by the v0.2 YouTube adapter."
    if obsolete in limitations:
        limitations.remove(obsolete)
    limitation = (
        "YouTube comment and reply extraction is best effort from selected public video pages; "
        "platform ordering, disabled comments, removals, restrictions, and extractor changes can limit coverage."
    )
    if limitation not in limitations:
        limitations.append(limitation)
    if warnings:
        manifest.setdefault("warnings", {})["youtube_comments"] = warnings
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_voice_packet(bundle: Path) -> None:
    voice = bundle / "voice"
    required = [voice / name for name in ("voice_task.md", "voice_taxonomy.json", "voice_results.csv", "voice_manifest.json")]
    if any(path.exists() for path in required):
        print("Existing customer-voice packet left unchanged. Review it, then rerun prepare_customer_voice.py --force if needed.")
        return
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/prepare_customer_voice.py"), "--bundle", str(bundle)],
        check=True,
    )
    print(f"Next Agent task: {voice / 'voice_task.md'}")


def main() -> int:
    args = parse_args()
    if args.top <= 0 or args.max_comments_per_video < 0 or args.sleep_requests < 0:
        raise SystemExit("--top must be positive; comment limit and request delay cannot be negative")
    try:
        _content_fields, content_rows = load_csv(
            args.bundle / "content.csv", {"record_id", "platform", "url", "views"}
        )
        selected = select_content(content_rows, args.video, args.top)
        command = resolve_ytdlp(args.ytdlp_path)
        version = ytdlp_version(command)
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    if not selected:
        raise SystemExit("No YouTube video records were selected from content.csv")

    comments_path = args.bundle / "comments.csv"
    comments = load_checkpoint(comments_path) if args.resume else {}
    warnings: list[str] = []
    status = "completed"
    processed = 0
    human_required = False

    print(f"Selected {len(selected)} YouTube videos from {args.bundle / 'content.csv'}")
    for index, content in enumerate(selected, start=1):
        print(f"Video {index}/{len(selected)}: {content['record_id']}", flush=True)
        try:
            raw_comments, warning = fetch_comments(
                command, content, args.max_comments_per_video, args.sleep_requests
            )
        except HumanVerificationRequired as exc:
            human_required = True
            status = "human_verification_required"
            warnings.append(f"{content['record_id']}: {str(exc).splitlines()[-1]}")
            break
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            status = "partial_with_errors"
            warnings.append(f"{content['record_id']}: {str(exc).splitlines()[-1]}")
            print(f"  Skipped: {exc}", file=sys.stderr)
            continue
        for row in comments_from_raw(raw_comments, content):
            comments[row["comment_id"]] = row
        if warning:
            warnings.append(f"{content['record_id']}: {warning.splitlines()[-1]}")
        write_csv(comments_path, COMMENT_FIELDS, sorted(comments.values(), key=lambda row: row["comment_id"]))
        processed += 1
        print(f"  Captured {len(raw_comments)} public comments/replies", flush=True)

    write_csv(comments_path, COMMENT_FIELDS, sorted(comments.values(), key=lambda row: row["comment_id"]))
    update_manifest(
        args.bundle,
        version,
        len(selected),
        len(comments),
        args.max_comments_per_video,
        status,
        warnings,
    )
    print(f"Captured {len(comments)} unique public comments/replies; processed {processed}/{len(selected)} videos.")
    print(f"Run status: {status}")
    print(f"Checkpoint: {comments_path}")
    if human_required:
        print("YouTube requested human verification. Do not bypass it; retry later after resolving access normally.")
        return 2
    if status != "completed":
        return 1
    if comments and not args.no_voice_packet:
        prepare_voice_packet(args.bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
