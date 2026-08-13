#!/usr/bin/env python3
"""Collect public Facebook Page posts through an authorized Chrome session."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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

# Facebook virtualizes long feeds: older articles can disappear from the DOM as
# the page moves. This expression reads only the current viewport window. Python
# accumulates and deduplicates every window before the next scroll.
EXTRACT_SCRIPT = r"""(() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const absolute = href => {
    try { return new URL(href, location.origin).href; } catch (_) { return ''; }
  };
  const linkScore = href => {
    const value = href || '';
    if (/story_fbid=|[?&]fbid=|[?&]v=/.test(value)) return 6;
    if (/\/posts\/(?:pfbid|\d+)/.test(value)) return 6;
    if (/\/(?:videos|reel)\/\d+/.test(value)) return 5;
    if (/\/photos\//.test(value)) return 4;
    if (/pfbid/.test(value)) return 3;
    return 0;
  };
  const articles = [...document.querySelectorAll('[role="article"]')].map(article => {
    const links = [...article.querySelectorAll('a[href]')]
      .map(node => ({
        href: absolute(node.getAttribute('href')),
        label: clean(node.getAttribute('aria-label') || node.getAttribute('title') || node.innerText)
      }))
      .filter(item => /^https?:\/\/([^/]+\.)?facebook\.com\//i.test(item.href))
      .sort((a, b) => linkScore(b.href) - linkScore(a.href));
    const permalink = links.length && linkScore(links[0].href) ? links[0].href : '';
    const message = article.querySelector('[data-ad-preview="message"], [data-ad-comet-preview="message"]');
    const text = clean(message ? message.innerText : article.innerText);
    const timeNode = article.querySelector('time[datetime], abbr[data-utime]');
    let publishedAt = '';
    if (timeNode) {
      publishedAt = timeNode.getAttribute('datetime') || '';
      const epoch = timeNode.getAttribute('data-utime');
      if (!publishedAt && epoch && /^\d+$/.test(epoch)) {
        publishedAt = new Date(Number(epoch) * 1000).toISOString();
      }
    }
    const permalinkNode = links.find(item => item.href === permalink);
    const publishedLabel = clean(
      (timeNode && (timeNode.getAttribute('aria-label') || timeNode.getAttribute('title') || timeNode.innerText)) ||
      (permalinkNode && permalinkNode.label) || ''
    );
    const metricLabels = [...article.querySelectorAll('[aria-label], [title]')]
      .flatMap(node => [node.getAttribute('aria-label'), node.getAttribute('title')])
      .map(clean)
      .filter(Boolean)
      .filter((value, index, all) => all.indexOf(value) === index)
      .filter(value => /\d/.test(value))
      .slice(0, 80);
    return { permalink, text, publishedAt, publishedLabel, metricLabels };
  }).filter(item => item.permalink && item.text);

  const bodyText = clean(document.body ? document.body.innerText.slice(0, 5000) : '');
  const challenge = /\/(checkpoint|captcha|challenge)\//i.test(location.href) ||
    /(security check|confirm your identity|human verification|验证码|安全检查|确认你的身份)/i.test(bodyText);
  return {
    url: location.href,
    title: document.title,
    scrollY: Math.round(window.scrollY || 0),
    scrollHeight: Math.round(document.documentElement.scrollHeight || document.body.scrollHeight || 0),
    challenge,
    articles
  };
})()"""


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF).strip()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "facebook-census"


def normalize_page_url(raw_url: str) -> str:
    parts = urlsplit(raw_url.strip())
    host = parts.netloc.lower().split(":", 1)[0]
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}:
        raise ValueError("Page URL must be on facebook.com")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    kept_query = {key: query[key] for key in ("id",) if key in query}
    return urlunsplit(("https", "www.facebook.com", path.rstrip("/") or "/", urlencode(kept_query), ""))


def account_handle(page_url: str) -> str:
    parts = urlsplit(page_url)
    query = dict(parse_qsl(parts.query))
    if parts.path.rstrip("/") == "/profile.php" and query.get("id"):
        return query["id"]
    segments = [segment for segment in parts.path.split("/") if segment]
    return segments[0] if segments else "facebook-page"


def canonicalize_post_url(raw_url: str) -> str:
    parts = urlsplit(clean_text(raw_url))
    host = parts.netloc.lower().split(":", 1)[0]
    if not host.endswith("facebook.com"):
        return ""
    host = "www.facebook.com"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    keep = {}
    for key in ("story_fbid", "fbid", "id", "v", "set", "type"):
        if query.get(key):
            keep[key] = query[key]
    return urlunsplit(("https", host, path, urlencode(keep), ""))


def post_key(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    for key in ("story_fbid", "fbid", "v"):
        if query.get(key):
            return query[key]
    patterns = (
        r"/posts/([^/?#]+)",
        r"/videos/(\d+)",
        r"/reel/(\d+)",
        r"/photos/(?:[^/]+/)?(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, parts.path)
        if match:
            return match.group(1)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def parse_compact_number(value: str) -> str:
    normalized = value.replace(",", "").strip().lower()
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


def metric_from_labels(labels: list[str], kind: str) -> str:
    patterns = {
        "likes": r"reaction|reacted|like|likes|心情|赞|တုံ့ပြန်",
        "comments": r"comment|comments|评论|မှတ်ချက်",
        "shares": r"share|shares|分享|မျှဝေ",
    }
    for label in labels:
        if re.search(patterns[kind], label, re.IGNORECASE):
            number = parse_compact_number(label)
            if number:
                return number
    return ""


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

    def open(self, page_url: str, bind: bool) -> None:
        if bind:
            self.run("bind")
        else:
            self.run("open", page_url)

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
            "OpenCLI is required for Facebook collection. Install @jackwener/opencli "
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


def article_to_row(article: dict[str, object], company: str, account: str, collected_at: str) -> dict[str, str] | None:
    url = canonicalize_post_url(clean_text(article.get("permalink")))
    text = clean_text(article.get("text"))
    if not url or not text:
        return None
    labels = [clean_text(label) for label in article.get("metricLabels", []) if clean_text(label)]
    return {
        "record_id": f"FB-{post_key(url)}",
        "platform": "Facebook",
        "account": account,
        "published_at": clean_text(article.get("publishedAt")),
        "language": "",
        "text_original": text,
        "text_translation": "",
        "views": "",
        "likes": metric_from_labels(labels, "likes"),
        "comments_count": metric_from_labels(labels, "comments"),
        "shares": metric_from_labels(labels, "shares"),
        "url": url,
        "content_type": "unclassified",
        "brand": company,
        "published_label": clean_text(article.get("publishedLabel")),
        "metric_labels": " | ".join(labels),
        "collected_at": collected_at,
        "retrieval_status": "captured",
    }


def row_score(row: dict[str, str]) -> tuple[int, int]:
    populated = sum(bool(row.get(field, "").strip()) for field in CONTENT_FIELDS)
    return populated, len(row.get("text_original", ""))


def merge_row(rows: dict[str, dict[str, str]], incoming: dict[str, str]) -> bool:
    current = rows.get(incoming["record_id"])
    if current is None:
        rows[incoming["record_id"]] = incoming
        return True
    merged = dict(current)
    for field, value in incoming.items():
        if value and (not merged.get(field) or (field == "text_original" and len(value) > len(merged[field]))):
            merged[field] = value
    if row_score(merged) > row_score(current) or merged != current:
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


def write_bundle(
    output_dir: Path,
    company: str,
    page_input: str,
    page_url: str,
    account: str,
    rows: dict[str, dict[str, str]],
    started_at: str,
    version: str,
    args: argparse.Namespace,
    status: str,
    page_title: str,
    rounds_completed: int,
    challenge_detected: bool,
) -> Path:
    ordered = sorted(
        rows.values(), key=lambda row: (row.get("published_at", ""), row["record_id"]), reverse=True
    )
    write_csv(output_dir / "content.csv", CONTENT_FIELDS, ordered)
    write_csv(output_dir / "comments.csv", COMMENT_FIELDS, [])
    last_active = next((row["published_at"] for row in ordered if row["published_at"]), "")
    note = (
        f"Best-effort visible Page-feed census; captured {len(ordered)} unique posts in "
        f"{rounds_completed} read rounds. Verify account identity and coverage before analysis."
    )
    write_csv(
        output_dir / "platform_census.csv",
        CENSUS_FIELDS,
        [
            {
                "platform": "Facebook",
                "handle": account,
                "url": page_url,
                "identity_status": "unverified",
                "identity_evidence": f"Browser page title: {page_title}" if page_title else "",
                "followers": "",
                "visible_items": str(len(ordered)),
                "last_active_at": last_active,
                "deep_dive": "yes",
                "notes": note,
            }
        ],
    )

    manifest = {
        "schema_version": "0.5",
        "research_mode": "competitor_intelligence",
        "target": {"company": company, "platform": "Facebook", "account": account},
        "input_url": page_input,
        "normalized_page_url": page_url,
        "started_at_utc": started_at,
        "cutoff_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "collector": {
            "name": "competitor-census Facebook connector",
            "version": "0.5.0",
            "browser_bridge": "OpenCLI",
            "browser_bridge_version": version,
        },
        "source_access": "publicly visible Page feed through the user's authorized Chrome session",
        "collection_controls": {
            "session": args.session,
            "bound_existing_tab": args.bind,
            "scroll_amount_pixels": args.scroll_amount,
            "delay_seconds": args.delay,
            "max_scrolls": args.max_scrolls,
            "max_posts": args.max_posts,
            "stagnant_rounds_before_stop": args.stagnant_rounds,
            "captcha_policy": "stop and require a human; never bypass",
            "write_actions": "none",
        },
        "status": status,
        "counts": {"unique_content": len(ordered), "comments": 0, "read_rounds": rounds_completed},
        "challenge_detected": challenge_detected,
        "field_notes": {
            "publication_date": "ISO value when exposed by the current DOM; otherwise published_label is preserved",
            "likes_comments_shares": "Point-in-time visible labels parsed when the current Facebook locale is recognized; raw labels are preserved",
            "views": "Not generally exposed for Page posts and therefore left blank",
            "translation_and_classification": "Left blank/unclassified for the Agent analysis phase",
        },
        "limitations": [
            "Account identity is not automatically verified.",
            "Facebook personalizes and virtualizes feeds; hidden, deleted, restricted, or not-returned posts may be absent.",
            "DOM structure and localized metric labels can change; blank metrics mean unavailable or unparsed, not zero.",
            "Comments and replies are not collected by this Page-post connector.",
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
            f"{company} Facebook census",
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
    parser.add_argument("--page", required=True, help="Public Facebook Page URL")
    parser.add_argument("--output", type=Path, help="Output directory (default: runs/<company>-facebook)")
    parser.add_argument("--session", default="competitor-census-fb", help="OpenCLI browser session name")
    parser.add_argument("--bind", action="store_true", help="Bind the currently focused Chrome tab instead of opening --page")
    parser.add_argument("--opencli", dest="opencli_path", help="Path to the OpenCLI executable")
    parser.add_argument("--max-scrolls", type=int, default=80, help="Maximum downward scrolls (default: 80)")
    parser.add_argument("--max-posts", type=int, default=0, help="Stop after N unique posts; 0 means no row limit")
    parser.add_argument("--scroll-amount", type=int, default=2400, help="Pixels per scroll (default: 2400)")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to wait after each scroll (default: 1.5)")
    parser.add_argument("--stagnant-rounds", type=int, default=6, help="Stop after N rounds with no new IDs and no page growth")
    parser.add_argument("--timeout", type=float, default=90, help="Per OpenCLI command timeout in seconds")
    parser.add_argument("--resume", action="store_true", help="Merge with an existing content.csv checkpoint")
    parser.add_argument("--keep-session", action="store_true", help="Leave the OpenCLI browser session open after a normal run")
    parser.add_argument("--no-report", action="store_true", help="Write evidence bundle only")
    parser.add_argument("--no-analysis-packet", action="store_true", help="Skip Agent analysis handoff files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_scrolls < 0 or args.max_posts < 0:
        raise SystemExit("--max-scrolls and --max-posts must be 0 or greater")
    if args.scroll_amount <= 0 or args.delay < 0 or args.stagnant_rounds <= 0 or args.timeout <= 0:
        raise SystemExit("scroll amount, stagnant rounds, and timeout must be positive; delay cannot be negative")

    try:
        page_url = normalize_page_url(args.page)
        command = resolve_opencli(args.opencli_path)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = args.output or ROOT / "runs" / f"{safe_slug(args.company)}-facebook"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_existing_rows(output_dir / "content.csv") if args.resume else {}
    account = account_handle(page_url)
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    browser = OpenCLIBrowser(command, args.session, args.timeout)
    version = opencli_version(command)
    status = "completed"
    challenge = False
    page_title = ""
    rounds_completed = 0
    stagnant = 0
    previous_extent: tuple[int, int] | None = None
    normal_finish = False

    print(f"Target: {args.company}")
    print(f"Facebook Page: {page_url}")
    print(f"OpenCLI: {version}")
    if rows:
        print(f"Resuming from {len(rows)} existing records")

    try:
        browser.open(page_url, args.bind)
        if args.delay:
            browser.wait(args.delay)

        total_rounds = args.max_scrolls + 1
        for round_index in range(total_rounds):
            payload = browser.extract()
            rounds_completed += 1
            page_title = clean_text(payload.get("title")) or page_title
            if payload.get("challenge"):
                challenge = True
                status = "human_verification_required"
                print("Human verification detected. Complete it manually; the connector will not bypass it.", file=sys.stderr)
                break

            before = len(rows)
            collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for raw in payload.get("articles", []):
                if isinstance(raw, dict):
                    row = article_to_row(raw, args.company, account, collected_at)
                    if row:
                        merge_row(rows, row)
            new_count = len(rows) - before
            write_csv(
                output_dir / "content.csv",
                CONTENT_FIELDS,
                sorted(rows.values(), key=lambda row: row["record_id"]),
            )
            print(f"Round {rounds_completed}: +{new_count}, {len(rows)} unique posts", flush=True)

            if args.max_posts and len(rows) >= args.max_posts:
                status = "row_limit_reached"
                break
            if round_index >= args.max_scrolls:
                status = "scroll_limit_reached" if args.max_scrolls else "single_window_complete"
                break

            extent = (int(payload.get("scrollY") or 0), int(payload.get("scrollHeight") or 0))
            stagnant = stagnant + 1 if new_count == 0 and extent == previous_extent else 0
            previous_extent = extent
            if stagnant >= args.stagnant_rounds:
                status = "feed_end_or_stagnant"
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
            args.page,
            page_url,
            account,
            rows,
            started_at,
            version,
            args,
            status,
            page_title,
            rounds_completed,
            challenge,
        )
        if not args.keep_session and not challenge:
            try:
                browser.release(args.bind)
            except Exception:
                pass

    if not rows:
        print("No Facebook post records were captured. Check the Page, login state, and browser bridge.", file=sys.stderr)
        return 1
    if normal_finish and not args.no_report:
        build_report(output_dir, args.company)
    analysis_task = None
    if normal_finish and not args.no_analysis_packet:
        analysis_task = prepare_analysis_packet(output_dir)

    print(f"Captured {len(rows)} unique public Facebook post records.")
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
