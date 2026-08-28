# YouTube adapter

Use `./public-web-census youtube` for a real, public-metadata vertical slice. It invokes `scripts/collect_youtube.py`, which uses `yt-dlp`, does not download media, and writes the standard Public Web Census evidence bundle. Use `./public-web-census youtube-comments` afterward to deep-read selected public video conversations into the same bundle.

`yt-dlp` is a YouTube-aware extractor, not a generic crawler. It already understands YouTube's public page structure and returns normalized metadata, which makes this connector simpler than the browser-backed TikTok, Facebook, and LinkedIn connectors. Those platforms remain on the authorized Chrome route because their feeds are rendered in-session and may require human verification.

## Install the collector dependency

Use a current official release:

```bash
python3 -m pip install -U "yt-dlp[default]"
```

If an extractor breaks, update yt-dlp before debugging the adapter.

## Trial run

```bash
./public-web-census youtube \
  --company "Example Company" \
  --channel "https://www.youtube.com/@ExampleCompany" \
  --tabs videos \
  --max-items-per-tab 10
```

## Best-effort selected-tab census

```bash
./public-web-census youtube \
  --company "Example Company" \
  --channel "https://www.youtube.com/@ExampleCompany" \
  --tabs videos,shorts,streams \
  --max-items-per-tab 0
```

Rich mode opens each public video page to obtain publication date, description, views, likes, and visible comment count where available. A large history can therefore take time. The adapter sleeps between metadata requests by default; increase `--sleep-requests` when appropriate.

## Outputs

The default directory is `runs/<company-slug>/`:

- `platform_census.csv`: one unverified YouTube account row for human identity review;
- `content.csv`: normalized point-in-time video metadata;
- `comments.csv`: header only after baseline collection; populated only after `youtube-comments` runs;
- `run_manifest.json`: scope, cutoff, selected tabs, tool version, counts, field limitations, and warnings;
- `summary.json`: deterministic descriptive statistics;
- `report.html`: baseline evidence-linked report.
- `analysis/analysis_task.md`: model-agnostic instructions for the next Agent step;
- `analysis/taxonomy.json`: empty emergent-taxonomy template;
- `analysis/analysis_results.csv`: one analysis placeholder for every source ID;
- `analysis/analysis_manifest.json`: input fingerprint and row-count contract.

## Interpretation boundary

The collector leaves `text_translation` blank and `content_type` as `unclassified`. After collection, ask an Agent to follow `analysis/analysis_task.md`, then run:

```bash
python3 scripts/apply_analysis.py --bundle runs/example-company
```

The validator writes `analyzed_content.csv` and `analysis_report.html` only after the translation, taxonomy, exact row coverage, source fingerprint, and representative evidence all pass. Verify account identity separately before using the conclusions.

Do not describe a limited run as a census. Even with `--max-items-per-tab 0`, use “best-effort selected-tab census at the cutoff” because deleted, private, members-only, personalized, age-restricted, and region-restricted content may be unavailable.

## Public conversations

Use the existing `yt-dlp` dependency to read public comments and replies from selected video pages. No Google API Key, browser login, cookie, or media download is required:

```bash
./public-web-census youtube-comments \
  --bundle runs/example-company \
  --top 30 \
  --max-comments-per-video 500
```

Repeat `--video <public-YouTube-video-URL>` to choose specific videos already present in `content.csv`. The default selects the 30 highest-view captured videos and retains up to 500 public comments and replies per video. Set `--max-comments-per-video 0` only when the stated scope calls for all retrievable public comments and replies for each selected video.

The command preserves the YouTube comment ID, parent relationship, public display author, visible timestamp when available, likes, source URL, and a deterministic official-reply marker. It writes checkpoints after every video and then prepares the existing customer-voice analysis packet. The extractor is best effort: comments may be disabled, deleted, restricted, reordered, or incompletely returned by YouTube or a future `yt-dlp` release. If YouTube asks for human verification, the command stops without bypassing it.
