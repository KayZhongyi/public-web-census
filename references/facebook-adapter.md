# Facebook Page connector

Use `./public-web-census facebook` to collect publicly visible posts from a Facebook Page into the standard evidence bundle. The connector owns the collection logic, stable-ID handling, checkpoints, schema, manifest, validation handoff, and report workflow. It uses the third-party [OpenCLI](https://github.com/jackwener/OpenCLI) browser bridge to read the user's authorized Chrome session.

## Prerequisites

1. Install Node.js 21 or newer.
2. Install OpenCLI:

   ```bash
   npm install -g @jackwener/opencli
   ```

3. Install and connect the OpenCLI Chrome extension according to the upstream instructions.
4. Log into Facebook normally in that Chrome profile.
5. Confirm the bridge before collection:

   ```bash
   opencli doctor
   ```

The collector does not receive, store, or export the Facebook password or browser cookies. Those remain in the user's browser and OpenCLI session. Never commit `runs/` or browser-session material.

## Start with a verification run

```bash
./public-web-census facebook \
  --company "Target Company" \
  --page "https://www.facebook.com/TargetPage" \
  --max-scrolls 3 \
  --output runs/target-facebook-check
```

Inspect:

- `content.csv`: source posts, dates or visible date labels, engagement fields, raw metric labels, and canonical URLs;
- `platform_census.csv`: account and coverage record;
- `run_manifest.json`: cutoff, controls, status, tool versions, limitations, and counts;
- `report.html`: baseline evidence view;
- `analysis/analysis_task.md`: model-agnostic next step.

Verify the Page identity, post links, dates, and a sample of visible metrics before increasing the scope.

## Run a deeper Page-feed census

```bash
./public-web-census facebook \
  --company "Target Company" \
  --page "https://www.facebook.com/TargetPage" \
  --max-scrolls 100 \
  --stagnant-rounds 8 \
  --output runs/target-facebook
```

Facebook removes older feed articles from the live DOM while scrolling. The connector therefore reads every visible DOM window before scrolling again, merges records by a platform identifier or canonical URL, and atomically rewrites `content.csv` after every round.

To continue an interrupted run:

```bash
./public-web-census facebook \
  --company "Target Company" \
  --page "https://www.facebook.com/TargetPage" \
  --output runs/target-facebook \
  --resume
```

If you manually opened the correct logged-in Page tab, focus it first and add `--bind`. The connector will attach to that tab instead of opening another one.

## Safety and stop behavior

- The connector reads only; it never likes, comments, follows, posts, or sends messages.
- It uses normal browser scrolling and does not attempt to bypass rate limits, authentication, or platform controls.
- If a human-verification or account-security challenge is detected, it stops and writes a partial checkpoint. A human may complete the challenge manually before restarting with `--resume`; the connector does not solve or bypass it.
- Repeated browser errors also stop the run. Partial data is marked as a checkpoint and does not automatically produce final analysis artifacts.
- Use a conservative scroll delay and stop if Facebook shows unusual security prompts or the declared scope is no longer valid.

## Coverage and field limitations

Call the result a “best-effort census of the in-scope public Page feed at the cutoff,” not a guaranteed copy of every historical post. Facebook can personalize, restrict, delete, reorder, or withhold records.

`published_at` is filled only when the current page exposes an ISO/epoch value. The untouched visible date label is preserved in `published_label`. Engagement parsers recognize common English, Chinese, and Myanmar labels; every discovered label is also preserved in `metric_labels` so an unknown locale can be audited or added without inventing a value. Blank metrics mean unavailable or unparsed, not zero.

This connector covers Page posts. Public comments and replies use a separate declared selection rule and are not collected by this command.

## Architecture and attribution

```text
public-web-census CLI
  -> Facebook collection and evidence rules (this repository)
  -> OpenCLI browser bridge (third-party Apache-2.0 dependency)
  -> user's authorized Chrome session
  -> standard CSV/JSON evidence bundle
  -> deterministic validation and Agent analysis
```

Public Web Census is not a fork or rebrand of OpenCLI. It calls OpenCLI as an installed dependency and adds the domain-specific collection, checkpoint, evidence, validation, and reporting layers in this repository. OpenCLI remains the work of its upstream authors under its own license.
