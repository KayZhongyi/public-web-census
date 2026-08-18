# Versioned evidence store

Use the SQLite workspace when evidence will be refreshed, compared, or reused by more than one person. Keep CSV/JSON as the portable exchange format.

## Storage model

Each workspace belongs to one declared target or topic and contains:

```text
project.json                 research contract
evidence.sqlite3             append-only evidence ledger
discovery/platform_census.csv
captures/<run-id>/           exact imported source bundles
changes/<run-id>.json        per-run change reports
current/                     latest materialized CSV/JSON view
```

The database records four layers:

- `runs`: cutoff, scope, manifest, source fingerprint, archived bundle, and table schemas;
- `entities`: stable platform, content, and comment identifiers with first/last observed runs;
- `observations`: one immutable source row per entity per run;
- `changes`: new, updated, unchanged, or not-observed status plus changed fields.

`current/` is derived and can be rebuilt. `evidence.sqlite3` plus `captures/` are the history and provenance record.

## Start a workspace

```bash
./public-web-census discover \
  --workspace runs/example \
  --target "Example Company" \
  --market "Singapore" \
  --purpose "Understand public product questions" \
  --owner "Market team"
```

Fill `discovery/platform_census.csv` while verifying accounts and source boundaries.

## Collect and ingest

Run a connector and ingest the successful bundle in one command:

```bash
./public-web-census collect youtube \
  --workspace runs/example \
  --company "Example Company" \
  --channel "https://www.youtube.com/@Example" \
  --output runs/example-youtube-2026-08
```

Or ingest any existing standard evidence bundle:

```bash
./public-web-census refresh \
  --workspace runs/example \
  --bundle runs/example-youtube-2026-08
```

Every import is fingerprinted and idempotent. Reimporting the same bundle does not create another run.

## Inspect and validate

```bash
./public-web-census diff --workspace runs/example
./public-web-census history --workspace runs/example --type content --id YT-example123
./public-web-census validate --workspace runs/example
./public-web-census snapshot --workspace runs/example
```

The change report separates content, engagement, and metadata changes. `not_observed` means absent from the latest same-scope capture; it never means deleted without a separate source check.

## Scope rules

The default scope key is derived from the verified target, normalized account URL, platform census row, and selected channel tabs. Date boundaries and captured record lists do not change scope identity. Use `--scope-key` when two intentionally different collection contracts point at the same account.

Do not mix targets in one workspace. Start a separate workspace when the subject, source population, or access boundary changes materially.

## Legacy compatibility

Existing `content.csv`, `comments.csv`, `platform_census.csv`, and `run_manifest.json` bundles remain valid inputs. `merge_incremental.py` remains available for a one-off portable CSV merge, but use the SQLite workspace for recurring monitoring because it retains every observation and archived source bundle.
