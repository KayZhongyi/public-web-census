# TikTok connector

## Scope

`./public-web-census tiktok` reads the publicly rendered profile grid through an authorized Chrome session connected by OpenCLI. It stores the canonical video URL and numeric ID, publication time derived from that ID, rendered thumbnail accessibility text, and the point-in-time profile-grid view count.

`./public-web-census tiktok-comments` opens selected videos already present in `content.csv`, reads public comments and visible replies, records parent-child relationships, and updates visible per-video likes, comment count, and shares when those metrics are exposed.

Both commands are read-only. They do not post, like, follow, message, download media, call private TikTok endpoints, replay signatures, or bypass access controls.

## Requirements

- Python 3.10 or later;
- current Chrome or Chromium;
- OpenCLI and its Chrome extension;
- an ordinary TikTok session that can view the target public page.

Run `./public-web-census doctor` before live collection.

## Start with a field check

```bash
./public-web-census tiktok \
  --company "Target Company" \
  --profile "@targethandle" \
  --max-scrolls 1 \
  --output runs/target-tiktok-check
```

Check the target identity and the coverage of `published_at`, `text_original`, `views`, and `url` in `content.csv`. Blank means unavailable, never zero.

## Best-effort profile census

```bash
./public-web-census tiktok \
  --company "Target Company" \
  --profile "@targethandle" \
  --max-scrolls 100 \
  --manual-scroll \
  --output runs/target-tiktok
```

TikTok may stop returning new cards to programmatic scrolling before the visible profile is exhausted. `--manual-scroll` keeps the same browser session and asks a human to scroll the ordinary public grid before the final extraction. This is a Human-in-the-Loop loading step, not a bypass. Describe the result as a best-effort capture of all retrievable public records inside the declared cutoff, not as a guarantee about hidden, deleted, restricted, personalized, or unreturned content.

Use `--resume` to merge a later attempt into the existing checkpoint by stable video ID.

## Public conversations

Select the top 30 captured videos by view count:

```bash
./public-web-census tiktok-comments \
  --bundle runs/target-tiktok \
  --top 30 \
  --owner "@targethandle" \
  --owner "Target Company"
```

Or repeat `--video` to choose particular URLs that already exist in `content.csv`.

Official identity requires the exact account handle/display name or a visible creator marker. The command never infers official status from tone, phone number, or reply content alone. Public usernames remain in the restricted evidence CSV; downstream customer-voice shareable reports replace them with stable aliases.

TikTok frequently presents a puzzle before comments load. The default behavior is to write a checkpoint, leave the page open, and return status `human_verification_required`. Complete the challenge yourself and rerun with `--resume`, or use `--wait-for-human` in an interactive terminal so the command pauses until you confirm manual completion. Never automate the puzzle.

## Field provenance

| Field | Source |
|---|---|
| `record_id` | `TT-` plus the public numeric video ID |
| `published_at` | UTC timestamp encoded in the high bits of the public numeric video ID |
| `text_original` | rendered thumbnail image accessibility text, with a known localized wrapper removed |
| `views` | rendered profile-grid view count |
| `likes`, `comments_count`, `shares` | visible video-page counters when the conversation command opens that video |
| `comment_id` | deterministic hash of content ID, parent ID, public username, and exact text |
| `parent_comment_id` | most recent visible level-one comment for a rendered level-two reply |
| `is_official` | exact owner identity or visible creator marker |

Selectors and localized accessibility wrappers can change. Keep the raw source URL, point-in-time labels, blank-field semantics, test fixtures, and manifest limitations so drift fails visibly instead of silently becoming a business conclusion.
