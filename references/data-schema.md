# Evidence bundle and ledger schema

Keep raw source fields immutable. Add translations and analysis fields in separate columns. CSV/JSON is the portable bundle contract; `evidence.sqlite3` is the optional versioned history for repeat use. See [versioned-evidence-store.md](versioned-evidence-store.md).

## Versioned observations

Each imported bundle creates one `runs` row and one immutable `observations` row per stable platform, content, or comment ID. The ledger keeps the exact archived bundle fingerprint, observed time, collection scope, raw row JSON, and change classification. `current/` is a rebuildable materialized view, not the historical source of truth.

## `platform_census.csv`

| Field | Meaning |
|---|---|
| `platform` | Channel or website |
| `handle` | Account identifier |
| `url` | Canonical account URL |
| `identity_status` | verified / probable / rejected / unresolved |
| `identity_evidence` | Language, address, domain, phone, or cross-link evidence |
| `followers` | Visible audience count |
| `visible_items` | Visible content count |
| `last_active_at` | Most recent visible activity |
| `deep_dive` | yes / no / pending |
| `notes` | Limits and ambiguity |

## `content.csv`

Required demo fields:

| Field | Meaning |
|---|---|
| `record_id` | Stable local or platform ID |
| `platform` | Source platform |
| `account` | Verified source account |
| `published_at` | ISO 8601 date/time when available |
| `language` | Original language code/name |
| `text_original` | Unmodified visible text |
| `text_translation` | Separate working translation |
| `views` | Plays/views, blank if unavailable |
| `likes` | Likes/reactions |
| `comments_count` | Visible comment count |
| `shares` | Shares/reposts, blank if unavailable |
| `url` | Source URL |
| `content_type` | Emergent category assigned after capture |
| `brand` | Visible promoted brand/product |

Production bundles should also include `collected_at`, `retrieval_status`, `media_type`, and `classification_notes`.

The YouTube adapter additionally writes `duration_seconds`, `channel_id`, `availability`, and `source_tab`. It leaves `text_translation` blank and `content_type` as `unclassified` until the Agent analysis phase. Platform fields that are not publicly exposed at collection time remain blank rather than becoming zero.

The Facebook Page connector additionally writes `published_label`, `metric_labels`, `collected_at`, and `retrieval_status`. `published_label` preserves the visible date text when Facebook does not expose a machine-readable date. `metric_labels` preserves the raw visible engagement labels used for parsing. Blank engagement fields mean unavailable or unparsed, not zero. See [facebook-adapter.md](facebook-adapter.md).

The LinkedIn connector uses the same additional fields. `published_label` preserves relative labels such as `2d` when an exact timestamp is not exposed. Visible reactions map to `likes`, reposts or shares map to `shares`, and impressions map to `views` when visible. A row without a visible post permalink uses the profile URL and `retrieval_status=captured_without_permalink`; see [linkedin-adapter.md](linkedin-adapter.md).

The v0.3 analysis handoff keeps this file unchanged. Agent output is validated separately, then merged into `analyzed_content.csv` with `classification_confidence` and `classification_notes`. See [analysis-handoff.md](analysis-handoff.md).

## `comments.csv`

Keep the header even when an adapter does not collect comments. An empty table means “not collected,” not “the content received zero comments.”

Collectors should leave translation, topic, commenter type, and response mode blank unless those values are deterministic source facts or come from a separately validated analysis. The fictional demo contains completed values only to demonstrate reporting.

| Field | Meaning |
|---|---|
| `comment_id` | Stable comment ID |
| `content_id` | Parent content record |
| `parent_comment_id` | Parent comment when this is a reply |
| `commenter` | Public display identifier; redact before publication |
| `commenter_type` | end_customer / installer_diy / reseller / epc_project / unknown |
| `is_official` | true only after deterministic identity check |
| `text_original` | Unmodified visible text |
| `text_translation` | Separate working translation |
| `published_at` | Comment publication timestamp when the source exposes one; blank otherwise |
| `likes` | Visible likes |
| `topic` | Emergent customer-need topic |
| `response_mode` | useful_answer / template / redirect / no_reply / not_applicable |
| `url` | Source URL |

## `run_manifest.json`

Record `research_mode`, target, market, date range, cutoff, timezone, platforms/accounts/queries checked, included scope, comment selection rule, tool versions, row counts, validation results, failures, and known limitations.

For a derived workspace snapshot, the manifest instead declares `artifact_type: derived_current_snapshot` and lists every source run and fingerprint used to construct the current view.

## Customer voice analysis outputs

`prepare_customer_voice.py` fingerprints `comments.csv` and contextual `content.csv`, then creates a separate `voice/` packet. `apply_customer_voice.py` validates the completed packet and never edits either source file.

### `voice/voice_results.csv`

| Field | Meaning |
|---|---|
| `comment_id` | Exactly one non-official source ID |
| `text_translation` | Faithful working translation |
| `issue_type` | Corpus-derived ID declared in `voice_taxonomy.json` |
| `signal_type` | question / complaint / request / praise / experience / other |
| `sentiment` | positive / neutral / negative / mixed / unclear |
| `severity` | informational / low / medium / high / critical |
| `analysis_confidence` | high / medium / low |
| `analysis_notes` | Ambiguity or observable severity justification |

### `analyzed_voice.csv`

Contains customer signals only, with validated analysis fields and visible official-reply links. It replaces `commenter` with a stable `commenter_alias`; the raw identifier remains only in the local evidence bundle.
