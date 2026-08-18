#!/usr/bin/env python3
"""Collect public TikTok comments and replies for selected evidence records."""

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


ROOT = Path(__file__).resolve().parents[1]
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
# Strategy: UI_SELECTOR + DOM_STATE
# Contract: visible-ui
# Evidence: the command uses the visible comment control and rendered comment
# tree. Structured OpenCLI clicks open the panel and public reply groups; page
# evaluation only reads the DOM. No private endpoint, signature, or CAPTCHA is
# replayed or bypassed.
EXTRACT_SCRIPT = r"""(() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const all = (root, selectors) => selectors.flatMap(selector => [...root.querySelectorAll(selector)]);
  const first = (root, selectors) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      if (node) return node;
    }
    return null;
  };
  const commentRoots = [...document.querySelectorAll('[data-e2e="comment-level-1"], [data-e2e="comment-level-2"]')];
  const fallbackRoots = commentRoots.length ? [] : [...document.querySelectorAll('[class*="DivCommentItemWrapper"]')];
  const roots = commentRoots.length ? commentRoots : fallbackRoots;
  const rows = [];
  for (const root of roots) {
    const nestedLevel = root.matches('[data-e2e="comment-level-2"]') ? 2 :
      (root.closest('[data-e2e="comment-level-2"]') ? 2 : 1);
    const parentRoot = nestedLevel === 2 ? root.closest('[data-e2e="comment-level-1"]') : null;
    const userLink = first(root, [
      '[data-e2e^="comment-username-"]',
      'a[href^="/@"]',
      '[class*="PCommentUsername"]',
      '[class*="SpanUniqueId"]'
    ]);
    const user = clean(userLink?.textContent || '');
    const userHref = userLink?.closest('a[href^="/@"]')?.getAttribute('href') ||
      (userLink?.matches?.('a[href^="/@"]') ? userLink.getAttribute('href') : '') || '';
    const textNode = first(root, [
      '[class*="PCommentText"]',
      '[class*="SpanCommentText"]',
      '[data-e2e="comment-content"]'
    ]);
    let text = clean(textNode?.textContent || '');
    if (!text) {
      const clone = root.cloneNode(true);
      all(clone, ['button', 'svg', '[class*="Like"]', '[class*="Footer"]']).forEach(node => node.remove());
      text = clean(clone.textContent || '');
      if (user && text.startsWith(user)) text = text.slice(user.length).trim();
    }
    const likeNode = first(root, [
      '[data-e2e="comment-like-count"]',
      '[class*="DivLikeContainer"] span',
      '[class*="SpanLikeCount"]'
    ]);
    const likeLabel = clean(likeNode?.getAttribute?.('aria-label') || likeNode?.textContent || '');
    const creator = /(创作者|作者|creator)/i.test(root.innerText || '');
    let parentUser = '';
    let parentText = '';
    if (parentRoot) {
      const parentUserNode = first(parentRoot, ['[data-e2e="comment-username-1"]', 'a[href^="/@"]']);
      const parentTextNode = first(parentRoot, ['[class*="PCommentText"]', '[class*="SpanCommentText"]', '[data-e2e="comment-content"]']);
      parentUser = clean(parentUserNode?.textContent || '');
      parentText = clean(parentTextNode?.textContent || '');
    }
    if (text && text !== '添加评论...' && text !== 'Add comment...') {
      rows.push({level: nestedLevel, user, userHref, text, likeLabel, creator, parentUser, parentText});
    }
  }
  const bodyText = clean(document.body ? document.body.innerText.slice(-10000) : '');
  const challenge = /(captcha|challenge|verify)/i.test(location.href) ||
    /(将拼图滑块拖动到相应位置|drag the puzzle|human verification|security check|验证码|安全检查)/i.test(bodyText);
  const metric = key => clean(document.querySelector(`[data-e2e="${key}"]`)?.textContent || '');
  return {
    url: location.href,
    title: document.title,
    challenge,
    rows,
    metrics: {likes: metric('like-count'), comments: metric('comment-count'), shares: metric('share-count')},
    replyExpanders: document.querySelectorAll('[data-e2e*="view-more"], [class*="DivViewReplies"]').length,
    commentPanel: !!document.querySelector('[data-e2e="comments"], [class*="DivCommentListContainer"]')
  };
})()"""


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF).strip()


def normalize_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).lower().lstrip("@"))


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

    def open(self, url: str) -> None:
        self.run("open", url)

    def wait(self, seconds: float) -> None:
        self.run("wait", "time", str(seconds))

    def extract(self) -> dict[str, object]:
        payload = self.run("eval", EXTRACT_SCRIPT, expect_json=True)
        if not isinstance(payload, dict):
            raise ValueError("OpenCLI extraction did not return an object")
        return payload

    def click_comments(self) -> None:
        self.run("click", '[data-e2e="comment-icon"]', "--nth", "0")

    def expand_replies(self) -> int:
        selector = '[data-e2e*="view-more"], [class*="DivViewReplies"]'
        try:
            payload = self.run("find", "--css", selector, "--limit", "100", "--text-max", "100", expect_json=True)
        except RuntimeError:
            return 0
        if not isinstance(payload, dict):
            return 0
        entries = payload.get("entries", [])
        refs = [str(entry.get("ref")) for entry in entries if isinstance(entry, dict) and entry.get("ref")]
        clicked = 0
        for ref in reversed(refs):
            try:
                self.run("click", ref)
                clicked += 1
            except RuntimeError:
                continue
        return clicked

    def scroll(self, amount: int) -> None:
        selector = '[class*="DivCommentListContainer"], [data-e2e="comments"]'
        try:
            payload = self.run("find", "--css", selector, "--limit", "10", "--text-max", "40", expect_json=True)
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            visible = [entry for entry in entries if isinstance(entry, dict) and entry.get("visible") and entry.get("ref")]
            if visible:
                self.run("focus", str(visible[0]["ref"]))
                self.run("keys", "PageDown")
                return
        except RuntimeError:
            pass
        self.run("scroll", "down", "--amount", str(amount))

    def close(self) -> None:
        self.run("close")


def resolve_opencli(explicit_path: str | None) -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"OpenCLI executable not found: {path}")
        return str(path)
    executable = shutil.which("opencli")
    if not executable:
        raise FileNotFoundError(
            "OpenCLI is required for TikTok comment collection. Install @jackwener/opencli "
            "and connect its Chrome extension first."
        )
    return executable


def load_content(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        required = {"record_id", "platform", "account", "views", "url"}
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    return fields, rows


def load_comments(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(COMMENT_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        return {row["comment_id"]: row for row in reader if row.get("comment_id")}


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def metric_number(value: str) -> int:
    parsed = parse_compact_number(value)
    return int(parsed) if parsed else 0


def selected_content(rows: list[dict[str, str]], explicit_urls: list[str], top: int) -> list[dict[str, str]]:
    candidates = [row for row in rows if row.get("platform", "").lower() == "tiktok" and "/video/" in row.get("url", "")]
    if explicit_urls:
        wanted = {url.split("?", 1)[0].rstrip("/") for url in explicit_urls}
        selected = [row for row in candidates if row["url"].split("?", 1)[0].rstrip("/") in wanted]
        missing = wanted - {row["url"].split("?", 1)[0].rstrip("/") for row in selected}
        if missing:
            raise ValueError("Video URLs are not present in content.csv: " + ", ".join(sorted(missing)))
        return selected
    return sorted(candidates, key=lambda row: metric_number(row.get("views", "")), reverse=True)[:top]


def comment_id(content_id: str, parent_id: str, user: str, text: str) -> str:
    digest = hashlib.sha256("\n".join((content_id, parent_id, user, text)).encode("utf-8")).hexdigest()[:20]
    return f"TTC-{digest}"


def is_official(raw: dict[str, object], owner_values: set[str]) -> bool:
    if raw.get("creator"):
        return True
    user = normalize_identity(clean_text(raw.get("user")))
    href = normalize_identity(clean_text(raw.get("userHref")).split("/", 2)[-1])
    return bool((user and user in owner_values) or (href and href in owner_values))


def raw_rows_to_comments(
    raw_rows: list[dict[str, object]], content: dict[str, str], owner_values: set[str]
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    latest_parent = ""
    parent_ids: dict[tuple[str, str], str] = {}
    for raw in raw_rows:
        user = clean_text(raw.get("user")) or clean_text(raw.get("userHref")).removeprefix("/@")
        text = clean_text(raw.get("text"))
        if not text:
            continue
        level = int(raw.get("level") or 1)
        parent_key = (clean_text(raw.get("parentUser")), clean_text(raw.get("parentText")))
        parent = (parent_ids.get(parent_key) or latest_parent) if level == 2 else ""
        cid = comment_id(content["record_id"], parent, user, text)
        official = is_official(raw, owner_values)
        result.append(
            {
                "comment_id": cid,
                "content_id": content["record_id"],
                "parent_comment_id": parent,
                "commenter": user,
                "commenter_type": "official" if official else "unclassified",
                "is_official": "true" if official else "false",
                "text_original": text,
                "text_translation": "",
                "likes": parse_compact_number(clean_text(raw.get("likeLabel"))),
                "topic": "",
                "response_mode": "",
                "url": content["url"],
            }
        )
        if level == 1:
            latest_parent = cid
            parent_ids[(user, text)] = cid
    return result


def update_manifest(bundle: Path, selected: int, comments: int, status: str, challenge: bool) -> None:
    path = bundle / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": "0.6"}
    manifest["conversation_collection"] = {
        "collector": "public-web-census TikTok conversation connector",
        "version": "0.6.0",
        "selected_content": selected,
        "public_comments_and_replies": comments,
        "status": status,
        "challenge_detected": challenge,
        "cutoff_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "captcha_policy": "pause for a human; never bypass",
        "write_actions": "none",
    }
    counts = manifest.setdefault("counts", {})
    counts["comments"] = comments
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Evidence-bundle directory containing content.csv")
    parser.add_argument("--top", type=int, default=30, help="Select the top N TikTok videos by captured views (default: 30)")
    parser.add_argument("--video", action="append", default=[], help="Collect a specific video URL from content.csv; repeatable")
    parser.add_argument("--owner", action="append", default=[], help="Official handle or display name; repeatable")
    parser.add_argument("--session", default="public-web-census-tt-comments", help="OpenCLI browser session name")
    parser.add_argument("--opencli", dest="opencli_path", help="Path to the OpenCLI executable")
    parser.add_argument("--max-scrolls-per-video", type=int, default=8, help="Comment-panel scroll attempts per video")
    parser.add_argument("--scroll-amount", type=int, default=1200, help="Pixels per comment scroll")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to wait after page actions")
    parser.add_argument("--stagnant-rounds", type=int, default=3, help="Stop a video after N comment rounds without new rows")
    parser.add_argument("--wait-for-human", action="store_true", help="On a challenge, wait for manual completion and then resume")
    parser.add_argument("--timeout", type=float, default=90, help="Per OpenCLI command timeout in seconds")
    parser.add_argument("--resume", action="store_true", help="Merge with an existing comments.csv checkpoint")
    parser.add_argument("--keep-session", action="store_true", help="Leave the browser session open")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top <= 0 or args.max_scrolls_per_video < 0 or args.scroll_amount <= 0 or args.delay < 0 or args.stagnant_rounds <= 0 or args.timeout <= 0:
        raise SystemExit("top, scroll amount, stagnant rounds, and timeout must be positive; other limits cannot be negative")
    if args.wait_for_human and not sys.stdin.isatty():
        raise SystemExit("--wait-for-human requires an interactive terminal")
    try:
        command = resolve_opencli(args.opencli_path)
        fields, content_rows = load_content(args.bundle / "content.csv")
        selected = selected_content(content_rows, args.video, args.top)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if not selected:
        raise SystemExit("No TikTok video records were selected from content.csv")

    comments_path = args.bundle / "comments.csv"
    comments = load_comments(comments_path) if args.resume else {}
    owner_values = {normalize_identity(value) for value in args.owner if normalize_identity(value)}
    owner_values.update(normalize_identity(row.get("account", "")) for row in selected if row.get("account"))
    browser = OpenCLIBrowser(command, args.session, args.timeout)
    challenge = False
    status = "completed"
    processed = 0
    content_by_id = {row["record_id"]: row for row in content_rows}

    print(f"Selected {len(selected)} TikTok videos from {args.bundle / 'content.csv'}")
    try:
        for index, content in enumerate(selected, 1):
            print(f"Video {index}/{len(selected)}: {content['record_id']}", flush=True)
            browser.open(content["url"])
            if args.delay:
                browser.wait(args.delay)
            payload = browser.extract()
            if payload.get("challenge"):
                if args.wait_for_human:
                    input("TikTok requires human verification. Complete it in Chrome, then press Enter here: ")
                    payload = browser.extract()
                if payload.get("challenge"):
                    challenge = True
                    status = "human_verification_required"
                    break
            if not payload.get("commentPanel"):
                browser.click_comments()
                if args.delay:
                    browser.wait(args.delay)
                payload = browser.extract()
            if payload.get("challenge"):
                if args.wait_for_human:
                    input("TikTok requires human verification. Complete it in Chrome, then press Enter here: ")
                    payload = browser.extract()
                if payload.get("challenge"):
                    challenge = True
                    status = "human_verification_required"
                    break

            stagnant = 0
            video_rows: dict[str, dict[str, str]] = {}
            for round_index in range(args.max_scrolls_per_video + 1):
                clicked = browser.expand_replies()
                if clicked and args.delay:
                    browser.wait(args.delay)
                payload = browser.extract()
                if payload.get("challenge"):
                    challenge = True
                    status = "human_verification_required"
                    break
                before = len(video_rows)
                for row in raw_rows_to_comments(
                    [raw for raw in payload.get("rows", []) if isinstance(raw, dict)], content, owner_values
                ):
                    video_rows[row["comment_id"]] = row
                new_count = len(video_rows) - before
                print(f"  Round {round_index + 1}: +{new_count}, {len(video_rows)} visible comments/replies", flush=True)
                stagnant = stagnant + 1 if new_count == 0 and clicked == 0 else 0
                if round_index >= args.max_scrolls_per_video or stagnant >= args.stagnant_rounds:
                    break
                browser.scroll(args.scroll_amount)
                if args.delay:
                    browser.wait(args.delay)
            for cid, row in video_rows.items():
                comments[cid] = row
            metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
            if isinstance(metrics, dict):
                target = content_by_id[content["record_id"]]
                for target_field, source_field in (("likes", "likes"), ("comments_count", "comments"), ("shares", "shares")):
                    parsed = parse_compact_number(clean_text(metrics.get(source_field)))
                    if parsed:
                        target[target_field] = parsed
            write_csv(comments_path, COMMENT_FIELDS, sorted(comments.values(), key=lambda row: row["comment_id"]))
            write_csv(args.bundle / "content.csv", fields, content_rows)
            processed += 1
            if challenge:
                break
    except KeyboardInterrupt:
        status = "interrupted_with_checkpoint"
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        status = "collector_error_with_checkpoint"
        print(f"Collection stopped: {exc}", file=sys.stderr)
    finally:
        write_csv(comments_path, COMMENT_FIELDS, sorted(comments.values(), key=lambda row: row["comment_id"]))
        write_csv(args.bundle / "content.csv", fields, content_rows)
        update_manifest(args.bundle, len(selected), len(comments), status, challenge)
        if not args.keep_session and not challenge:
            try:
                browser.close()
            except Exception:
                pass

    print(f"Captured {len(comments)} unique public comments/replies; processed {processed}/{len(selected)} videos.")
    print(f"Run status: {status}")
    print(f"Checkpoint: {comments_path}")
    if challenge:
        print("Complete the challenge manually, then rerun with --resume. The connector will not bypass it.")
        return 2
    if comments:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/prepare_customer_voice.py"), "--bundle", str(args.bundle)],
            check=True,
        )
        print(f"Next Agent task: {args.bundle / 'voice' / 'voice_task.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
