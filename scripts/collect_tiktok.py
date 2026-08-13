#!/usr/bin/env python3
"""Collect public TikTok profile videos through an authorized Chrome session."""

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
    "published_label",
    "metric_labels",
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

# Strategy note
# Strategy: DOM_STATE
# Contract: visible-ui
# Evidence: TikTok's public profile grid exposes a canonical /video/<id> link,
# accessibility text on the thumbnail image, and data-e2e="video-views". The
# connector reads only those rendered fields. It does not call private APIs,
# replay signatures, or bypass platform controls.
EXTRACT_SCRIPT = r"""(() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const compact = selector => clean(document.querySelector(selector)?.textContent || '');
  const items = [...document.querySelectorAll('[data-e2e="user-post-item"]')].map(item => {
    const link = item.querySelector('a[href*="/video/"]');
    const image = item.querySelector('img[alt]');
    const views = item.querySelector(
      '[data-e2e="video-views"], strong[class*="VideoCount"], strong[class*="video-count"], [class*="StrongVideoCount"]'
    );
    const badge = item.querySelector('[data-e2e="video-card-badge"]');
    const fallbackViews = clean(item.innerText || '').split(' ')
      .find(value => /^\d+(?:\.\d+)?(?:[KMB]|千|万|百万|亿)?$/i.test(value)) || '';
    return {
      url: link ? link.href : '',
      accessibilityText: image ? clean(image.alt) : '',
      viewsLabel: views ? clean(views.textContent) : fallbackViews,
      badge: badge ? clean(badge.textContent) : ''
    };
  }).filter(item => /\/video\/\d+/.test(item.url));
  const bodyText = clean(document.body ? document.body.innerText.slice(-6000) : '');
  const challenge = /(captcha|challenge|verify)/i.test(location.href) ||
    /(将拼图滑块拖动到相应位置|drag the puzzle|human verification|security check|验证码|安全检查)/i.test(bodyText);
  return {
    url: location.href,
    title: document.title,
    displayName: compact('[data-e2e="user-title"]'),
    handle: compact('[data-e2e="user-subtitle"]'),
    followersLabel: compact('[data-e2e="followers-count"]'),
    likesLabel: compact('[data-e2e="likes-count"]'),
    scrollY: Math.round(window.scrollY || 0),
    scrollHeight: Math.round(document.documentElement.scrollHeight || document.body.scrollHeight || 0),
    challenge,
    items
  };
})()"""


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF).strip()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "tiktok-census"


def normalize_profile_url(raw_url: str) -> str:
    value = raw_url.strip()
    if re.fullmatch(r"@?[A-Za-z0-9._-]+", value):
        value = f"https://www.tiktok.com/@{value.lstrip('@')}"
    parts = urlsplit(value)
    host = parts.netloc.lower().split(":", 1)[0]
    if host not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        raise ValueError("Profile must be a TikTok handle or tiktok.com profile URL")
    match = re.match(r"^/@([^/?#]+)", parts.path)
    if not match:
        raise ValueError("TikTok profile URL must look like https://www.tiktok.com/@handle")
    handle = match.group(1)
    return urlunsplit(("https", "www.tiktok.com", f"/@{handle}", "", ""))


def account_handle(profile_url: str) -> str:
    return urlsplit(profile_url).path.removeprefix("/@").split("/", 1)[0]


def canonicalize_video_url(raw_url: str) -> str:
    parts = urlsplit(clean_text(raw_url))
    if not parts.netloc.lower().endswith("tiktok.com"):
        return ""
    match = re.search(r"/@([^/?#]+)/video/(\d+)", parts.path)
    if not match:
        return ""
    return f"https://www.tiktok.com/@{match.group(1)}/video/{match.group(2)}"


def video_id_from_url(url: str) -> str:
    match = re.search(r"/video/(\d+)", url)
    return match.group(1) if match else ""


def published_at_from_video_id(video_id: str) -> str:
    try:
        seconds = int(video_id) >> 32
        if not 1_262_304_000 <= seconds <= 4_102_444_800:
            return ""
        return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def parse_compact_number(value: str) -> str:
    normalized = clean_text(value).replace(",", "").lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(k|m|b|千|万|百万|亿)?", normalized)
    if not match:
        return ""
    number = float(match.group(1))
    multiplier = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "千": 1_000,
        "万": 10_000,
        "百万": 1_000_000,
        "亿": 100_000_000,
    }[match.group(2) or ""]
    return str(round(number * multiplier))


def caption_from_accessibility_text(value: str, display_name: str, handle: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    # TikTok localizes the accessibility prefix. Preserve the original text if
    # no known delimiter is present rather than guessing and losing evidence.
    for marker in ("创作的 ", " created by ", " posted by "):
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    for prefix in (display_name, f"@{handle}", handle):
        if prefix and text == prefix:
            return ""
    return text.strip()


def parse_opencli_json(stdout: str) -> object:
    payload = stdout.strip()
    if not payload:
        raise ValueError("OpenCLI returned empty output")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        starts = [index for index in (payload.find("{"), payload.find("[")) if index >= 0]
        if not starts:
            raise ValueError("OpenCLI did not return JSON")
        start = min(starts)
        for end in range(len(payload), start, -1):
            try:
                return json.loads(payload[start:end])
            except json.JSONDecodeError:
                continue
        raise ValueError("OpenCLI returned malformed JSON")


class OpenCLIBrowser:
    def __init__(self, command: str, session: str, timeout: float) -> None:
        self.command = command
        self.session = session
        self.timeout = timeout

    def run(self, *args: str, expect_json: bool = False) -> object | str:
        completed = subprocess.run(
            [self.command, "browser", self.session, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f"OpenCLI exited with status {completed.returncode}")
        return parse_opencli_json(completed.stdout) if expect_json else completed.stdout.strip()

    def open(self, profile_url: str, bind: bool) -> None:
        self.run("bind" if bind else "open", *(() if bind else (profile_url,)))

    def extract(self) -> dict[str, object]:
        payload = self.run("eval", EXTRACT_SCRIPT, expect_json=True)
        if not isinstance(payload, dict):
            raise ValueError("OpenCLI extraction did not return an object")
        return payload

    def scroll(self, amount: int) -> None:
        self.run("scroll", "down", "--amount", str(amount))

    def wait(self, seconds: float) -> None:
        self.run("wait", "time", str(seconds))

    def release(self, bound: bool) -> None:
        self.run("unbind" if bound else "close")


def resolve_opencli(explicit_path: str | None) -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"OpenCLI executable not found: {path}")
        return str(path)
    executable = shutil.which("opencli")
    if not executable:
        raise FileNotFoundError(
            "OpenCLI is required for TikTok collection. Install @jackwener/opencli "
            "and connect its Chrome extension first."
        )
    return executable


def opencli_version(command: str) -> str:
    completed = subprocess.run(
        [command, "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    output = (completed.stdout or completed.stderr).strip()
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else "unknown"


def item_to_row(
    item: dict[str, object], company: str, account: str, display_name: str, collected_at: str
) -> dict[str, str] | None:
    url = canonicalize_video_url(clean_text(item.get("url")))
    video_id = video_id_from_url(url)
    if not url or not video_id:
        return None
    raw_accessibility = clean_text(item.get("accessibilityText"))
    caption = caption_from_accessibility_text(raw_accessibility, display_name, account)
    badge = clean_text(item.get("badge"))
    labels = [value for value in (f"views:{clean_text(item.get('viewsLabel'))}", f"badge:{badge}") if value.split(":", 1)[1]]
    return {
        "record_id": f"TT-{video_id}",
        "platform": "TikTok",
        "account": account,
        "published_at": published_at_from_video_id(video_id),
        "language": "",
        "text_original": caption,
        "text_translation": "",
        "views": parse_compact_number(clean_text(item.get("viewsLabel"))),
        "likes": "",
        "comments_count": "",
        "shares": "",
        "url": url,
        "content_type": "unclassified",
        "brand": company,
        "published_label": "",
        "metric_labels": " | ".join(labels),
        "collected_at": collected_at,
        "retrieval_status": "captured",
    }


def merge_row(rows: dict[str, dict[str, str]], incoming: dict[str, str]) -> bool:
    current = rows.get(incoming["record_id"])
    if current is None:
        rows[incoming["record_id"]] = incoming
        return True
    merged = dict(current)
    for field, value in incoming.items():
        if value and (not merged.get(field) or (field == "text_original" and len(value) > len(merged[field]))):
            merged[field] = value
    rows[incoming["record_id"]] = merged
    return False


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def load_existing_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(CONTENT_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: cannot resume; missing columns: {', '.join(sorted(missing))}")
        return {row["record_id"]: row for row in reader if row.get("record_id")}


def csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def write_bundle(
    output_dir: Path,
    company: str,
    profile_input: str,
    profile_url: str,
    account: str,
    rows: dict[str, dict[str, str]],
    started_at: str,
    version: str,
    args: argparse.Namespace,
    status: str,
    page_title: str,
    display_name: str,
    followers: str,
    rounds_completed: int,
    challenge_detected: bool,
) -> Path:
    ordered = sorted(
        rows.values(), key=lambda row: (row.get("published_at", ""), row["record_id"]), reverse=True
    )
    write_csv(output_dir / "content.csv", CONTENT_FIELDS, ordered)
    if not (output_dir / "comments.csv").exists():
        write_csv(output_dir / "comments.csv", COMMENT_FIELDS, [])
    last_active = next((row["published_at"] for row in ordered if row["published_at"]), "")
    note = (
        f"Best-effort visible profile-grid census; captured {len(ordered)} unique videos in "
        f"{rounds_completed} read rounds. Verify identity and compare visible coverage before analysis."
    )
    write_csv(
        output_dir / "platform_census.csv",
        CENSUS_FIELDS,
        [
            {
                "platform": "TikTok",
                "handle": account,
                "url": profile_url,
                "identity_status": "unverified",
                "identity_evidence": " | ".join(
                    value for value in (f"Display name: {display_name}" if display_name else "", f"Page title: {page_title}" if page_title else "") if value
                ),
                "followers": followers,
                "visible_items": str(len(ordered)),
                "last_active_at": last_active,
                "deep_dive": "yes",
                "notes": note,
            }
        ],
    )
    comments_count = csv_row_count(output_dir / "comments.csv")
    manifest = {
        "schema_version": "0.6",
        "research_mode": "competitor_intelligence",
        "target": {"company": company, "platform": "TikTok", "account": account},
        "input_url": profile_input,
        "normalized_profile_url": profile_url,
        "started_at_utc": started_at,
        "cutoff_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "collector": {
            "name": "competitor-census TikTok connector",
            "version": "0.6.0",
            "browser_bridge": "OpenCLI",
            "browser_bridge_version": version,
            "strategy": "visible DOM state",
        },
        "source_access": "publicly visible profile grid through the user's authorized Chrome session",
        "collection_controls": {
            "session": args.session,
            "bound_existing_tab": args.bind,
            "manual_scroll": args.manual_scroll,
            "scroll_amount_pixels": args.scroll_amount,
            "delay_seconds": args.delay,
            "max_scrolls": args.max_scrolls,
            "max_videos": args.max_videos,
            "stagnant_rounds_before_stop": args.stagnant_rounds,
            "captcha_policy": "stop and require a human; never bypass",
            "write_actions": "none",
        },
        "status": status,
        "counts": {"unique_content": len(ordered), "comments": comments_count, "read_rounds": rounds_completed},
        "challenge_detected": challenge_detected,
        "field_notes": {
            "publication_date": "Derived deterministically from the timestamp bits in the public numeric video ID and stored in UTC",
            "views": "Point-in-time value exposed in the public profile grid; coverage is reported and blank means unavailable, not zero",
            "caption": "Extracted from the rendered thumbnail accessibility text; the raw prefix is only removed when a known localized delimiter is present",
            "likes_comments_shares": "Not exposed by the profile grid and therefore left blank; use the conversation connector for selected videos",
            "translation_and_classification": "Left blank/unclassified for the Agent analysis phase",
        },
        "limitations": [
            "Account identity is not automatically verified.",
            "TikTok personalizes and lazily loads profiles; hidden, deleted, restricted, or not-returned videos may be absent.",
            "A stagnant automatic scroll is not proof that every public video was returned; use --manual-scroll when visual coverage requires human confirmation.",
            "Blank engagement fields mean unavailable in this collection surface, not zero.",
            "A stopped or challenged run may contain a valid partial checkpoint but is not a completed census.",
        ],
    }
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


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
            f"{company} TikTok census",
            "--dataset-kind",
            "live",
            "--quiet",
        ],
        check=True,
    )


def prepare_analysis_packet(output_dir: Path) -> Path:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/prepare_analysis.py"), "--bundle", str(output_dir)],
        check=True,
    )
    return output_dir / "analysis" / "analysis_task.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, help="Company or target label used in output")
    parser.add_argument("--profile", required=True, help="TikTok @handle or public profile URL")
    parser.add_argument("--output", type=Path, help="Output directory (default: runs/<company>-tiktok)")
    parser.add_argument("--session", default="competitor-census-tt", help="OpenCLI browser session name")
    parser.add_argument("--bind", action="store_true", help="Bind the focused Chrome tab instead of opening --profile")
    parser.add_argument("--manual-scroll", action="store_true", help="Wait for a human to scroll the visible profile before final extraction")
    parser.add_argument("--opencli", dest="opencli_path", help="Path to the OpenCLI executable")
    parser.add_argument("--max-scrolls", type=int, default=80, help="Maximum automatic downward scrolls (default: 80)")
    parser.add_argument("--max-videos", type=int, default=0, help="Stop after N unique videos; 0 means no row limit")
    parser.add_argument("--scroll-amount", type=int, default=2400, help="Pixels per scroll (default: 2400)")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to wait after each scroll (default: 1.5)")
    parser.add_argument("--stagnant-rounds", type=int, default=6, help="Stop after N rounds with no new IDs and no page growth")
    parser.add_argument("--timeout", type=float, default=90, help="Per OpenCLI command timeout in seconds")
    parser.add_argument("--resume", action="store_true", help="Merge with an existing content.csv checkpoint")
    parser.add_argument("--keep-session", action="store_true", help="Leave the browser session open after a normal run")
    parser.add_argument("--no-report", action="store_true", help="Write evidence bundle only")
    parser.add_argument("--no-analysis-packet", action="store_true", help="Skip Agent analysis handoff files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_scrolls < 0 or args.max_videos < 0:
        raise SystemExit("--max-scrolls and --max-videos must be 0 or greater")
    if args.scroll_amount <= 0 or args.delay < 0 or args.stagnant_rounds <= 0 or args.timeout <= 0:
        raise SystemExit("scroll amount, stagnant rounds, and timeout must be positive; delay cannot be negative")
    if args.manual_scroll and not sys.stdin.isatty():
        raise SystemExit("--manual-scroll requires an interactive terminal")

    try:
        profile_url = normalize_profile_url(args.profile)
        command = resolve_opencli(args.opencli_path)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = args.output or ROOT / "runs" / f"{safe_slug(args.company)}-tiktok"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_existing_rows(output_dir / "content.csv") if args.resume else {}
    account = account_handle(profile_url)
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    browser = OpenCLIBrowser(command, args.session, args.timeout)
    version = opencli_version(command)
    status = "completed"
    challenge = False
    page_title = ""
    display_name = ""
    followers = ""
    rounds_completed = 0
    stagnant = 0
    previous_extent: tuple[int, int] | None = None
    normal_finish = False
    manual_prompted = False

    print(f"Target: {args.company}")
    print(f"TikTok profile: {profile_url}")
    print(f"OpenCLI: {version}")
    if rows:
        print(f"Resuming from {len(rows)} existing records")

    try:
        browser.open(profile_url, args.bind)
        if args.delay:
            browser.wait(args.delay)
        total_rounds = args.max_scrolls + 1
        for round_index in range(total_rounds):
            payload = browser.extract()
            rounds_completed += 1
            page_title = clean_text(payload.get("title")) or page_title
            display_name = clean_text(payload.get("displayName")) or display_name
            followers = parse_compact_number(clean_text(payload.get("followersLabel"))) or followers
            if payload.get("challenge"):
                challenge = True
                status = "human_verification_required"
                print("Human verification detected. Complete it manually; the connector will not bypass it.", file=sys.stderr)
                break

            before = len(rows)
            collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for raw in payload.get("items", []):
                if isinstance(raw, dict):
                    row = item_to_row(raw, args.company, account, display_name, collected_at)
                    if row:
                        merge_row(rows, row)
            new_count = len(rows) - before
            write_csv(output_dir / "content.csv", CONTENT_FIELDS, sorted(rows.values(), key=lambda row: row["record_id"]))
            print(f"Round {rounds_completed}: +{new_count}, {len(rows)} unique videos", flush=True)

            if args.max_videos and len(rows) >= args.max_videos:
                status = "row_limit_reached"
                break
            if round_index >= args.max_scrolls:
                status = "scroll_limit_reached" if args.max_scrolls else "single_window_captured"
                break

            extent = (int(payload.get("scrollY") or 0), int(payload.get("scrollHeight") or 0))
            stagnant = stagnant + 1 if new_count == 0 and extent == previous_extent else 0
            previous_extent = extent
            if stagnant >= args.stagnant_rounds:
                if args.manual_scroll and not manual_prompted:
                    manual_prompted = True
                    input(
                        "Automatic loading is stagnant. In the opened TikTok profile, manually scroll through "
                        "the publicly visible grid, then press Enter here to capture the final DOM: "
                    )
                    payload = browser.extract()
                    rounds_completed += 1
                    if payload.get("challenge"):
                        challenge = True
                        status = "human_verification_required"
                    else:
                        collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                        for raw in payload.get("items", []):
                            if isinstance(raw, dict):
                                row = item_to_row(raw, args.company, account, display_name, collected_at)
                                if row:
                                    merge_row(rows, row)
                        status = "manual_scroll_confirmed"
                    break
                status = "profile_end_or_stagnant"
                break
            browser.scroll(args.scroll_amount)
            if args.delay:
                browser.wait(args.delay)
        normal_finish = not challenge
    except KeyboardInterrupt:
        status = "interrupted_with_checkpoint"
        print("Interrupted; writing the current checkpoint.", file=sys.stderr)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        status = "collector_error_with_checkpoint"
        print(f"Collection stopped: {exc}", file=sys.stderr)
    finally:
        manifest_path = write_bundle(
            output_dir,
            args.company,
            args.profile,
            profile_url,
            account,
            rows,
            started_at,
            version,
            args,
            status,
            page_title,
            display_name,
            followers,
            rounds_completed,
            challenge,
        )
        if not args.keep_session and not challenge:
            try:
                browser.release(args.bind)
            except Exception:
                pass

    if not rows:
        print("No TikTok video records were captured. Check the profile, login state, and browser bridge.", file=sys.stderr)
        return 2 if challenge else 1
    if normal_finish and not args.no_report:
        build_report(output_dir, args.company)
    analysis_task = None
    if normal_finish and not args.no_analysis_packet:
        analysis_task = prepare_analysis_packet(output_dir)

    print(f"Captured {len(rows)} unique public TikTok video records.")
    print(f"Run status: {status}")
    print(f"Evidence bundle: {output_dir}")
    print(f"Manifest: {manifest_path}")
    if not normal_finish:
        print("Only a checkpoint was written; resolve the stop condition before generating final analysis.")
    if analysis_task:
        print(f"Next Agent task: {analysis_task}")
    return 0 if normal_finish else 2


if __name__ == "__main__":
    raise SystemExit(main())
