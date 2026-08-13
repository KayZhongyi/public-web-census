# YouTube adapter

Use `./competitor-census youtube` for a real, public-metadata vertical slice. It invokes `scripts/collect_youtube.py`, which uses `yt-dlp`, does not download media, and writes the standard Competitor Census evidence bundle.

## Install the collector dependency

Use a current official release:

```bash
python3 -m pip install -U "yt-dlp[default]"
```

If an extractor breaks, update yt-dlp before debugging the adapter.

## Trial run

```bash
./competitor-census youtube \
  --company "Example Company" \
  --channel "https://www.youtube.com/@ExampleCompany" \
  --tabs videos \
  --max-items-per-tab 10
```

## Best-effort selected-tab census

```bash
./competitor-census youtube \
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
- `comments.csv`: header only; this adapter does not collect comments or replies;
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
