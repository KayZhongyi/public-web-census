# Incremental updates

Use a new run directory for every capture. Never overwrite the previous evidence bundle before validation. For recurring use, prefer the versioned workspace described in [versioned-evidence-store.md](versioned-evidence-store.md).

## Versioned workspace

```bash
./public-web-census discover \
  --workspace runs/target \
  --target "Target Company" \
  --purpose "Track material public changes"

./public-web-census refresh \
  --workspace runs/target \
  --bundle runs/target-2026-07

./public-web-census diff --workspace runs/target
./public-web-census validate --workspace runs/target
```

This path archives every imported bundle, retains every observation, and rebuilds `current/` without deleting records absent from a bounded refresh.

## Date-bounded YouTube capture

```bash
python3 scripts/collect_youtube.py \
  --company "Target Company" \
  --channel "https://www.youtube.com/@TargetHandle" \
  --tabs videos,shorts,streams \
  --since 2026-07-01 \
  --output runs/target-2026-07
```

The adapter passes the date boundary to the collector and records it in `run_manifest.json`.

## Portable stable-ID merge

```bash
python3 scripts/merge_incremental.py \
  --base runs/target-baseline/content.csv \
  --incoming runs/target-2026-07/content.csv \
  --output runs/target-current/content.csv
```

Use this legacy path when a one-off CSV result is sufficient and a historical workspace is unnecessary. For comments, add `--id-field comment_id`. The tool requires identical schemas, retains base rows absent from the incoming scope, and reports new, updated, unchanged, and absent records.

Absence from a bounded incoming run is not proof of deletion. Verify the source separately before marking a record removed or unavailable.

Read [monitoring-playbook.md](monitoring-playbook.md) before turning a repeat run into an alert or group notification.
