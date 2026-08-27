#!/usr/bin/env python3
"""Collect visible LinkedIn company or personal-profile posts through Chrome."""

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
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTENT_FIELDS = [
    "record_id", "platform", "account", "published_at", "language",
    "text_original", "text_translation", "views", "likes", "comments_count",
    "shares", "url", "content_type", "brand", "published_label",
    "metric_labels", "collected_at", "retrieval_status",
]
CENSUS_FIELDS = [
    "platform", "handle", "url", "identity_status", "identity_evidence",
    "followers", "visible_items", "last_active_at", "deep_dive", "notes",
]
COMMENT_FIELDS = [
    "comment_id", "content_id", "parent_comment_id", "commenter",
    "commenter_type", "is_official", "text_original", "text_translation",
    "likes", "topic", "response_mode", "url",
]

# Read-only DOM extraction. LinkedIn virtualizes long feeds, so Python keeps a
# stable-ID ledger across successive viewport windows.
EXTRACT_SCRIPT = r"""(() => {
  const clean = value => String(value || '').replace(/[\u00a0\u202f]+/g, ' ').replace(/\s+/g, ' ').trim();
  const absolute = href => { try { return new URL(href, location.origin).href; } catch (_) { return ''; } };
  const cards = [...document.querySelectorAll('article, [role="article"], .feed-shared-update-v2, .occludable-update')]
    .filter(card => clean(card.innerText || card.textContent).length > 40);
  const posts = cards.map(card => {
    const links = [...card.querySelectorAll('a[href]')].map(link => ({
      href: absolute(link.getAttribute('href')),
      text: clean(link.innerText || link.textContent),
      label: clean(link.getAttribute('aria-label') || link.getAttribute('title'))
    }));
    const permalink = links.find(link => /\/feed\/update\/|\/posts\/|\/pulse\//.test(link.href))?.href || '';
    const authorLink = links.find(link => /linkedin\.com\/(?:in|company)\//.test(link.href));
    const rawText = clean(card.innerText || card.textContent);
    const bodyNode = card.querySelector(
      '.feed-shared-update-v2__description, .update-components-text, .feed-shared-text, [class*="update-components-text"]'
    );
    const body = clean(bodyNode ? bodyNode.innerText : '');
    const timeNode = card.querySelector('time, a[href*="/feed/update/"] span[aria-hidden="true"]');
    const publishedLabel = clean(timeNode ? timeNode.innerText : '') ||
      (rawText.match(/\b\d+\s*(?:s|m|h|d|w|mo|yr|min)\b/i) || [''])[0];
    const metricLabels = [...card.querySelectorAll('[aria-label], [title]')]
      .flatMap(node => [node.getAttribute('aria-label'), node.getAttribute('title')])
      .map(clean)
      .filter(value => value && /\d/.test(value))
      .filter((value, index, all) => all.indexOf(value) === index)
      .slice(0, 60);
    return {
      permalink,
      author: clean(authorLink ? authorLink.innerText : ''),
      body: body || rawText,
      publishedLabel,
      metricLabels,
      rawText
    };
  }).filter(post => post.body || post.permalink);
  const pageText = clean(document.body ? document.body.innerText.slice(0, 5000) : '');
  const challenge = /\/(checkpoint|challenge|authwall)\//i.test(location.href) ||
    /(security verification|verify your identity|let's do a quick security check|验证码|安全验证)/i.test(pageText);
  const signedOut = /\/login|\/authwall/i.test(location.href) || /(sign in to linkedin|join linkedin)/i.test(pageText);
  return {
    url: location.href,
    title: document.title,
    challenge,
    signedOut,
    scrollY: Math.round(window.scrollY || 0),
    scrollHeight: Math.round(document.documentElement.scrollHeight || document.body.scrollHeight || 0),
    posts
  };
})()"""


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF).strip()


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "linkedin-census"


def normalize_profile_url(raw_url: str) -> tuple[str, str, str]:
    value = raw_url.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://www.linkedin.com/{value.lstrip('/')}"
    parts = urlsplit(value)
    host = parts.netloc.lower().split(":", 1)[0]
    if host not in {"linkedin.com", "www.linkedin.com"}:
        raise ValueError("Profile URL must be on linkedin.com")
    match = re.match(r"^/(in|company)/([^/?#]+)", parts.path)
    if not match:
        raise ValueError("Use a LinkedIn /in/<person> or /company/<company> URL")
    kind, handle = match.groups()
    canonical = f"https://www.linkedin.com/{kind}/{handle}/"
    activity = (
        f"{canonical}recent-activity/all/"
        if kind == "in"
        else f"{canonical}posts/?feedView=all"
    )
    return canonical, activity, handle


def parse_compact_number(value: str) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(k|m|b|千|万|百万|亿)?", value.lower().replace(",", ""))
    if not match:
        return ""
    number = float(match.group(1))
    multiplier = {
        "": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
        "千": 1_000, "万": 10_000, "百万": 1_000_000, "亿": 100_000_000,
    }[match.group(2) or ""]
    return str(round(number * multiplier))


def metric_value(labels: list[str], raw_text: str, kind: str) -> str:
    patterns = {
        "likes": r"reaction|reactions|like|likes|赞|反应",
        "comments": r"comment|comments|评论",
        "shares": r"repost|reposts|share|shares|转发|分享",
        "views": r"impression|impressions|view|views|展示|浏览",
    }
    candidates = [*labels, raw_text]
    for candidate in candidates:
        match = re.search(
            rf"(\d[\d,.]*\s*(?:k|m|b|千|万|百万|亿)?)\s*{patterns[kind]}",
            candidate,
            re.IGNORECASE,
        )
        if match:
            return parse_compact_number(match.group(1))
    return ""


def canonical_post_url(raw_url: str) -> str:
    parts = urlsplit(clean_text(raw_url))
    if parts.netloc.lower().split(":", 1)[0] not in {"linkedin.com", "www.linkedin.com"}:
        return ""
    if not re.search(r"/feed/update/|/posts/|/pulse/", parts.path):
        return ""
    return f"https://www.linkedin.com{parts.path.rstrip('/')}/"


def stable_post_id(url: str, author: str, published_label: str, body: str) -> str:
    match = re.search(r"(?:activity[:-]|activity%3A)(\d{8,})", url, re.IGNORECASE)
    if match:
        return match.group(1)
    digest = hashlib.sha256(f"{url}|{author}|{published_label}|{body}".encode("utf-8")).hexdigest()
    return digest[:20]


def post_to_row(
    post: dict[str, object], company: str, account: str, profile_url: str, collected_at: str
) -> dict[str, str] | None:
    body = clean_text(post.get("body"))
    author = clean_text(post.get("author"))
    published_label = clean_text(post.get("publishedLabel"))
    url = canonical_post_url(clean_text(post.get("permalink")))
    if not body and not url:
        return None
    labels = [clean_text(label) for label in post.get("metricLabels", []) if clean_text(label)]
    raw_text = clean_text(post.get("rawText"))
    stable_id = stable_post_id(url, author, published_label, body)
    return {
        "record_id": f"LI-{stable_id}",
        "platform": "LinkedIn",
        "account": account,
        "published_at": "",
        "language": "",
        "text_original": body,
        "text_translation": "",
        "views": metric_value(labels, raw_text, "views"),
        "likes": metric_value(labels, raw_text, "likes"),
        "comments_count": metric_value(labels, raw_text, "comments"),
        "shares": metric_value(labels, raw_text, "shares"),
        "url": url or profile_url,
        "content_type": "unclassified",
        "brand": company,
        "published_label": published_label,
        "metric_labels": " | ".join(labels),
        "collected_at": collected_at,
        "retrieval_status": "captured" if url else "captured_without_permalink",
    }


def parse_json(stdout: str) -> object:
    payload = stdout.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenCLI did not return valid JSON") from exc


class Browser:
    def __init__(self, command: str, session: str, timeout: float) -> None:
        self.command, self.session, self.timeout = command, session, timeout

    def run(self, *args: str, json_output: bool = False) -> object | str:
        result = subprocess.run(
            [self.command, "browser", self.session, *args],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=self.timeout,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "OpenCLI failed")
        return parse_json(result.stdout) if json_output else result.stdout.strip()

    def open(self, url: str, bind: bool) -> None:
        self.run("bind" if bind else "open", *(() if bind else (url,)))

    def extract(self) -> dict[str, object]:
        payload = self.run("eval", EXTRACT_SCRIPT, json_output=True)
        if not isinstance(payload, dict):
            raise ValueError("LinkedIn extraction returned a non-object payload")
        return payload

    def release(self, bind: bool) -> None:
        self.run("unbind" if bind else "close")


def resolve_opencli(explicit: str | None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"OpenCLI executable not found: {path}")
    command = shutil.which("opencli")
    if command:
        return command
    raise FileNotFoundError("OpenCLI is required for LinkedIn collection. Run ./public-web-census setup first.")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_bundle(
    output: Path, args: argparse.Namespace, profile_url: str, account: str,
    rows: dict[str, dict[str, str]], status: str, title: str, rounds: int,
) -> None:
    ordered = sorted(rows.values(), key=lambda row: (row["published_label"], row["record_id"]), reverse=True)
    write_csv(output / "content.csv", CONTENT_FIELDS, ordered)
    write_csv(output / "comments.csv", COMMENT_FIELDS, [])
    write_csv(output / "platform_census.csv", CENSUS_FIELDS, [{
        "platform": "LinkedIn", "handle": account, "url": profile_url,
        "identity_status": "unverified", "identity_evidence": f"Browser page title: {title}" if title else "",
        "followers": "", "visible_items": str(len(ordered)), "last_active_at": "", "deep_dive": "yes",
        "notes": "Best-effort visible post census; profile identity and coverage require human review.",
    }])
    manifest = {
        "schema_version": "0.5",
        "research_mode": "competitor_intelligence",
        "target": {"company": args.company, "platform": "LinkedIn", "account": account},
        "normalized_profile_url": profile_url,
        "cutoff_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "collector": {"name": "public-web-census LinkedIn connector", "version": "0.1.0", "browser_bridge": "OpenCLI"},
        "source_access": "visible profile or company posts through the user's authorized Chrome session",
        "collection_controls": {
            "bound_existing_tab": args.bind, "max_scrolls": args.max_scrolls,
            "max_posts": args.max_posts, "captcha_policy": "stop and require a human; never bypass",
            "write_actions": "none",
        },
        "status": status,
        "counts": {"unique_content": len(ordered), "comments": 0, "read_rounds": rounds},
        "limitations": [
            "Account identity is not automatically verified.",
            "LinkedIn personalizes and virtualizes feeds; hidden, deleted, restricted, or not-returned posts may be absent.",
            "Relative publication labels are preserved when an exact timestamp is not visible.",
            "Comment counts may be captured, but comment bodies and replies are not collected.",
            "Posts without a visible permalink use the profile URL and a content fingerprint as their stable local identity.",
        ],
    }
    (output / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, help="Company or target label")
    parser.add_argument("--profile", required=True, help="LinkedIn /company/<name> or /in/<person> URL")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--session", default="public-web-census-linkedin", help="OpenCLI browser session")
    parser.add_argument("--bind", action="store_true", help="Use the currently focused Chrome tab")
    parser.add_argument("--opencli", dest="opencli_path", help="Path to OpenCLI")
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--max-posts", type=int, default=100)
    parser.add_argument("--scroll-amount", type=int, default=2200)
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--stagnant-rounds", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--keep-session", action="store_true")
    parser.add_argument("--no-analysis-packet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if min(args.max_scrolls, args.max_posts) < 0 or args.scroll_amount <= 0 or args.delay < 0:
        raise SystemExit("scroll and row limits cannot be negative; scroll amount must be positive")
    if args.stagnant_rounds <= 0 or args.timeout <= 0:
        raise SystemExit("stagnant rounds and timeout must be positive")
    try:
        profile_url, activity_url, account = normalize_profile_url(args.profile)
        command = resolve_opencli(args.opencli_path)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    output = args.output or ROOT / "runs" / f"{safe_slug(args.company)}-linkedin"
    output.mkdir(parents=True, exist_ok=True)
    browser = Browser(command, args.session, args.timeout)
    rows: dict[str, dict[str, str]] = {}
    status, title, rounds, stagnant = "completed", "", 0, 0
    previous_extent: tuple[int, int] | None = None
    challenged = False
    try:
        browser.open(activity_url, args.bind)
        if args.delay:
            browser.run("wait", "time", str(args.delay))
        for index in range(args.max_scrolls + 1):
            payload = browser.extract()
            rounds += 1
            title = clean_text(payload.get("title")) or title
            if payload.get("challenge") or payload.get("signedOut"):
                status, challenged = "human_verification_required", True
                print("LinkedIn sign-in or verification is required. Complete it in Chrome; no challenge is bypassed.", file=sys.stderr)
                break
            before = len(rows)
            collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for raw in payload.get("posts", []):
                if isinstance(raw, dict):
                    row = post_to_row(raw, args.company, account, profile_url, collected_at)
                    if row:
                        current = rows.get(row["record_id"])
                        if current is None or len(row["text_original"]) > len(current["text_original"]):
                            rows[row["record_id"]] = row
            print(f"Round {rounds}: +{len(rows) - before}, {len(rows)} unique posts", flush=True)
            if args.max_posts and len(rows) >= args.max_posts:
                status = "row_limit_reached"
                break
            if index >= args.max_scrolls:
                status = "scroll_limit_reached" if args.max_scrolls else "single_window_complete"
                break
            extent = (int(payload.get("scrollY") or 0), int(payload.get("scrollHeight") or 0))
            stagnant = stagnant + 1 if len(rows) == before and extent == previous_extent else 0
            previous_extent = extent
            if stagnant >= args.stagnant_rounds:
                status = "feed_end_or_stagnant"
                break
            browser.run("scroll", "down", "--amount", str(args.scroll_amount))
            if args.delay:
                browser.run("wait", "time", str(args.delay))
    except KeyboardInterrupt:
        status = "interrupted_with_checkpoint"
    finally:
        write_bundle(output, args, profile_url, account, rows, status, title, rounds)
        if not args.keep_session:
            try:
                browser.release(args.bind)
            except Exception:
                pass
    if challenged:
        return 2
    if not rows:
        print("No visible LinkedIn posts were captured. Verify the profile, login, and feed visibility.", file=sys.stderr)
        return 1
    if not args.no_analysis_packet:
        subprocess.run([sys.executable, str(ROOT / "scripts/prepare_analysis.py"), "--bundle", str(output)], check=True)
    print(f"Captured {len(rows)} visible LinkedIn posts -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
