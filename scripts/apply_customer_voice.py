#!/usr/bin/env python3
"""Validate customer-voice analysis and render a redacted, evidence-linked report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from prepare_analysis import file_sha256
from prepare_customer_voice import RESULT_FIELDS, SCHEMA_VERSION


CONFIDENCE_VALUES = {"high", "medium", "low"}
SIGNAL_TYPES = {"question", "complaint", "request", "praise", "experience", "other"}
SENTIMENT_VALUES = {"positive", "neutral", "negative", "mixed", "unclear"}
SEVERITY_VALUES = {"informational", "low", "medium", "high", "critical"}
ISSUE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class VoiceValidationError(ValueError):
    def __init__(self, report: dict[str, object]):
        self.report = report
        super().__init__("Customer voice analysis did not pass validation")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def duplicate_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def is_official(row: dict[str, str]) -> bool:
    return row.get("is_official", "").strip().lower() in {"true", "1", "yes"}


def pseudonym(value: str) -> str:
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
    return f"voice-{digest}"


def validate_taxonomy(
    taxonomy: object,
    source_ids: set[str],
    result_by_id: dict[str, dict[str, str]],
) -> tuple[set[str], dict[str, str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    labels: dict[str, str] = {}
    if not isinstance(taxonomy, dict):
        return set(), labels, ["voice_taxonomy.json must contain a JSON object"], warnings
    if taxonomy.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"voice_taxonomy.json schema_version must be {SCHEMA_VERSION}")
    if not nonempty_string(taxonomy.get("target_language")):
        errors.append("voice_taxonomy.json target_language is required")
    if not nonempty_string(taxonomy.get("derivation_notes")):
        errors.append("voice_taxonomy.json derivation_notes must explain how issues emerged")

    issues = taxonomy.get("issues")
    if not isinstance(issues, list) or not issues:
        return set(), labels, errors + ["voice_taxonomy.json must define at least one issue"], warnings

    issue_ids: list[str] = []
    representatives: dict[str, list[str]] = {}
    for index, issue in enumerate(issues, start=1):
        prefix = f"voice taxonomy issue {index}"
        if not isinstance(issue, dict):
            errors.append(f"{prefix} must be an object")
            continue
        issue_id = str(issue.get("id", "")).strip()
        issue_ids.append(issue_id)
        if not ISSUE_ID_PATTERN.fullmatch(issue_id):
            errors.append(
                f"{prefix} id must use lowercase letters, numbers, hyphens, or underscores"
            )
        for field in ("label", "definition"):
            if not nonempty_string(issue.get(field)):
                errors.append(f"{prefix} {field} is required")
        labels[issue_id] = str(issue.get("label", issue_id)).strip()
        for field in ("inclusion_criteria", "exclusion_criteria", "representative_comment_ids"):
            value = issue.get(field)
            if not isinstance(value, list) or not value or not all(
                nonempty_string(item) for item in value
            ):
                errors.append(f"{prefix} {field} must be a non-empty string list")
        representative_ids = issue.get("representative_comment_ids", [])
        if isinstance(representative_ids, list):
            clean_ids = [str(value).strip() for value in representative_ids]
            representatives[issue_id] = clean_ids
            unknown = sorted(set(clean_ids) - source_ids)
            if unknown:
                errors.append(f"{prefix} has unknown representative IDs: {', '.join(unknown)}")

    if duplicates := duplicate_values(issue_ids):
        errors.append(f"voice taxonomy has duplicate issue IDs: {', '.join(duplicates)}")
    issue_set = set(issue_ids)
    used = Counter(row.get("issue_type", "").strip() for row in result_by_id.values())
    if unused := sorted(issue_set - set(used)):
        warnings.append(f"voice taxonomy issues have no assigned signals: {', '.join(unused)}")
    for issue_id, ids in representatives.items():
        mismatched = [
            comment_id
            for comment_id in ids
            if comment_id in result_by_id
            and result_by_id[comment_id].get("issue_type", "").strip() != issue_id
        ]
        if mismatched:
            errors.append(
                f"representative IDs assigned to a different issue for {issue_id}: "
                + ", ".join(mismatched)
            )
    return issue_set, labels, errors, warnings


def validate(
    bundle: Path,
) -> tuple[
    list[str],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, object],
    dict[str, str],
]:
    comments_path = bundle / "comments.csv"
    content_path = bundle / "content.csv"
    voice_dir = bundle / "voice"
    comment_fields, comments = load_csv(comments_path)
    _content_fields, content = load_csv(content_path)
    result_fields, results = load_csv(voice_dir / "voice_results.csv")
    manifest = load_json(voice_dir / "voice_manifest.json")
    taxonomy = load_json(voice_dir / "voice_taxonomy.json")
    if not isinstance(manifest, dict):
        raise ValueError("voice_manifest.json must contain a JSON object")

    errors: list[str] = []
    warnings: list[str] = []
    all_comment_ids = [row.get("comment_id", "").strip() for row in comments]
    if duplicates := duplicate_values(all_comment_ids):
        errors.append(f"comments.csv has duplicate comment IDs: {', '.join(duplicates)}")
    customer_rows = [row for row in comments if not is_official(row)]
    source_ids = [row.get("comment_id", "").strip() for row in customer_rows]
    result_ids = [row.get("comment_id", "").strip() for row in results]
    if duplicates := duplicate_values(result_ids):
        errors.append(f"voice_results.csv has duplicate comment IDs: {', '.join(duplicates)}")
    if result_fields != RESULT_FIELDS:
        errors.append("voice_results.csv columns or order changed; regenerate the voice packet")
    source_id_set = set(source_ids)
    result_id_set = set(result_ids)
    if missing := sorted(source_id_set - result_id_set):
        errors.append(f"voice_results.csv is missing comment IDs: {', '.join(missing)}")
    if unknown := sorted(result_id_set - source_id_set):
        errors.append(f"voice_results.csv has unknown comment IDs: {', '.join(unknown)}")
    if len(results) != len(customer_rows):
        errors.append(
            f"voice analysis row count {len(results)} does not match customer row count "
            f"{len(customer_rows)}"
        )

    current_source_hash = file_sha256(comments_path)
    current_context_hash = file_sha256(content_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"voice_manifest.json schema_version must be {SCHEMA_VERSION}")
    if manifest.get("research_mode") != "customer_voice":
        errors.append("voice_manifest.json research_mode must be customer_voice")
    if manifest.get("source_sha256") != current_source_hash:
        errors.append("comments.csv changed after the voice packet was prepared")
    if manifest.get("context_sha256") != current_context_hash:
        errors.append("content.csv changed after the voice packet was prepared")
    if manifest.get("source_rows") != len(comments):
        errors.append("voice manifest source row count does not match comments.csv")
    if manifest.get("customer_rows") != len(customer_rows):
        errors.append("voice manifest customer row count does not match comments.csv")

    result_by_id = {row.get("comment_id", "").strip(): row for row in results}
    issue_ids, issue_labels, taxonomy_errors, taxonomy_warnings = validate_taxonomy(
        taxonomy, source_id_set, result_by_id
    )
    errors.extend(taxonomy_errors)
    warnings.extend(taxonomy_warnings)
    if isinstance(taxonomy, dict) and (
        taxonomy.get("target_language") != manifest.get("target_language")
    ):
        errors.append("voice taxonomy target_language does not match the prepared manifest")

    for row_number, row in enumerate(results, start=2):
        comment_id = row.get("comment_id", "").strip() or f"row {row_number}"
        if not row.get("text_translation", "").strip():
            errors.append(f"{comment_id}: text_translation is required")
        issue_type = row.get("issue_type", "").strip()
        if not issue_type:
            errors.append(f"{comment_id}: issue_type is required")
        elif issue_type not in issue_ids:
            errors.append(f"{comment_id}: unknown issue_type {issue_type}")
        signal_type = row.get("signal_type", "").strip().lower()
        if signal_type not in SIGNAL_TYPES:
            errors.append(f"{comment_id}: invalid signal_type {signal_type or '(blank)'}")
        sentiment = row.get("sentiment", "").strip().lower()
        if sentiment not in SENTIMENT_VALUES:
            errors.append(f"{comment_id}: invalid sentiment {sentiment or '(blank)'}")
        severity = row.get("severity", "").strip().lower()
        if severity not in SEVERITY_VALUES:
            errors.append(f"{comment_id}: invalid severity {severity or '(blank)'}")
        confidence = row.get("analysis_confidence", "").strip().lower()
        if confidence not in CONFIDENCE_VALUES:
            errors.append(f"{comment_id}: confidence must be high, medium, or low")
        notes = row.get("analysis_notes", "").strip()
        if confidence == "low" and not notes:
            errors.append(f"{comment_id}: low confidence requires analysis_notes")
        if severity in {"high", "critical"} and not notes:
            errors.append(f"{comment_id}: {severity} severity requires observable justification")

    report = {
        "schema_version": SCHEMA_VERSION,
        "validated_at": utc_now(),
        "status": "failed" if errors else "passed",
        "source_file": "comments.csv",
        "source_sha256": current_source_hash,
        "context_sha256": current_context_hash,
        "source_rows": len(comments),
        "customer_rows": len(customer_rows),
        "analysis_rows": len(results),
        "translation_coverage": (
            sum(bool(row.get("text_translation", "").strip()) for row in results) / len(results)
            if results
            else 0
        ),
        "issue_coverage": (
            sum(bool(row.get("issue_type", "").strip()) for row in results) / len(results)
            if results
            else 0
        ),
        "issues": dict(
            sorted(Counter(row.get("issue_type", "").strip() for row in results).items())
        ),
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        raise VoiceValidationError(report)
    return comment_fields, comments, content, results, report, issue_labels


def write_json_atomic(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def reply_summary(
    comments: list[dict[str, str]], customer_ids: set[str]
) -> dict[str, dict[str, object]]:
    replies: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in comments:
        parent_id = row.get("parent_comment_id", "").strip()
        if is_official(row) and parent_id in customer_ids:
            replies[parent_id].append(row)
    return {
        comment_id: {
            "official_reply_count": len(rows),
            "official_response_modes": sorted(
                {
                    row.get("response_mode", "").strip()
                    for row in rows
                    if row.get("response_mode", "").strip()
                }
            ),
            "official_reply_ids": [row.get("comment_id", "").strip() for row in rows],
        }
        for comment_id, rows in replies.items()
    }


def write_analyzed_voice(
    path: Path,
    comments: list[dict[str, str]],
    results: list[dict[str, str]],
) -> list[dict[str, str]]:
    result_by_id = {row["comment_id"].strip(): row for row in results}
    customer_rows = [row for row in comments if not is_official(row)]
    customer_ids = {row["comment_id"].strip() for row in customer_rows}
    replies = reply_summary(comments, customer_ids)
    output_fields = [
        "comment_id",
        "content_id",
        "parent_comment_id",
        "commenter_alias",
        "commenter_type",
        "text_original",
        "text_translation",
        "likes",
        "issue_type",
        "signal_type",
        "sentiment",
        "severity",
        "analysis_confidence",
        "analysis_notes",
        "official_reply_count",
        "official_response_modes",
        "official_reply_ids",
        "url",
    ]
    analyzed: list[dict[str, str]] = []
    for source in customer_rows:
        comment_id = source["comment_id"].strip()
        result = result_by_id[comment_id]
        response = replies.get(
            comment_id,
            {
                "official_reply_count": 0,
                "official_response_modes": [],
                "official_reply_ids": [],
            },
        )
        analyzed.append(
            {
                "comment_id": comment_id,
                "content_id": source.get("content_id", "").strip(),
                "parent_comment_id": source.get("parent_comment_id", "").strip(),
                "commenter_alias": pseudonym(source.get("commenter", "")),
                "commenter_type": source.get("commenter_type", "").strip(),
                "text_original": source.get("text_original", ""),
                "text_translation": result.get("text_translation", "").strip(),
                "likes": source.get("likes", "").strip(),
                "issue_type": result.get("issue_type", "").strip(),
                "signal_type": result.get("signal_type", "").strip().lower(),
                "sentiment": result.get("sentiment", "").strip().lower(),
                "severity": result.get("severity", "").strip().lower(),
                "analysis_confidence": result.get("analysis_confidence", "").strip().lower(),
                "analysis_notes": result.get("analysis_notes", "").strip(),
                "official_reply_count": str(response["official_reply_count"]),
                "official_response_modes": "|".join(response["official_response_modes"]),
                "official_reply_ids": "|".join(response["official_reply_ids"]),
                "url": source.get("url", "").strip(),
            }
        )

    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(analyzed)
    os.replace(temp, path)
    return analyzed


def build_summary(
    bundle: Path,
    content: list[dict[str, str]],
    analyzed: list[dict[str, str]],
    issue_labels: dict[str, str],
) -> dict[str, object]:
    content_by_id = {row.get("record_id", "").strip(): row for row in content}
    issue_counts = Counter(row["issue_type"] for row in analyzed)
    signal_counts = Counter(row["signal_type"] for row in analyzed)
    sentiment_counts = Counter(row["sentiment"] for row in analyzed)
    severity_counts = Counter(row["severity"] for row in analyzed)
    response_modes = Counter()
    visible_response_count = 0
    useful_response_count = 0
    for row in analyzed:
        modes = [mode for mode in row["official_response_modes"].split("|") if mode]
        if int(row["official_reply_count"]):
            visible_response_count += 1
        if "useful_answer" in modes:
            useful_response_count += 1
        response_modes.update(modes)

    issue_evidence: dict[str, list[str]] = defaultdict(list)
    for row in analyzed:
        issue_evidence[row["issue_type"]].append(row["comment_id"])
    dated_counts: Counter[str] = Counter()
    for row in analyzed:
        context = content_by_id.get(row["content_id"], {})
        published = context.get("published_at", "")[:7]
        if published:
            dated_counts[published] += 1

    target = bundle.name
    manifest_path = bundle / "run_manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        if isinstance(manifest, dict):
            target_info = manifest.get("target")
            if isinstance(target_info, dict):
                target = str(
                    target_info.get("company")
                    or target_info.get("name")
                    or target_info.get("brand")
                    or target
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "research_mode": "customer_voice",
        "target": target,
        "customer_signals": len(analyzed),
        "issue_counts": dict(issue_counts.most_common()),
        "issue_labels": issue_labels,
        "signal_type_counts": dict(signal_counts.most_common()),
        "sentiment_counts": dict(sentiment_counts.most_common()),
        "severity_counts": dict(severity_counts.most_common()),
        "high_or_critical": sum(
            count for severity, count in severity_counts.items() if severity in {"high", "critical"}
        ),
        "visible_response_count": visible_response_count,
        "useful_response_count": useful_response_count,
        "response_mode_counts": dict(response_modes.most_common()),
        "context_month_counts": dict(sorted(dated_counts.items())),
        "issue_evidence": {
            issue: ids[:5] for issue, ids in sorted(issue_evidence.items())
        },
    }


def pct(count: int, total: int) -> str:
    return f"{count / total * 100:.0f}%" if total else "n/a"


def render_report(
    summary: dict[str, object],
    analyzed: list[dict[str, str]],
    content: list[dict[str, str]],
) -> str:
    total = int(summary["customer_signals"])
    issue_counts = summary["issue_counts"]
    issue_labels = summary["issue_labels"]
    assert isinstance(issue_counts, dict)
    assert isinstance(issue_labels, dict)
    top_issue = next(iter(issue_counts), "")
    top_issue_count = int(issue_counts.get(top_issue, 0))
    issue_rows = "\n".join(
        f"<tr><td><strong>{html.escape(str(issue_labels.get(issue, issue)))}</strong></td>"
        f"<td>{count}/{total}</td><td>{pct(int(count), total)}</td>"
        f"<td>{', '.join(f'<code>{html.escape(comment_id)}</code>' for comment_id in summary['issue_evidence'].get(issue, []))}</td></tr>"
        for issue, count in issue_counts.items()
    )

    content_by_id = {row.get("record_id", "").strip(): row for row in content}
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
    evidence_rows = "\n".join(
        f"""<tr>
          <td><code>{html.escape(row['comment_id'])}</code></td>
          <td>{html.escape(row['commenter_alias'])}</td>
          <td>{html.escape(row['text_translation'][:220])}</td>
          <td>{html.escape(str(issue_labels.get(row['issue_type'], row['issue_type'])))}</td>
          <td>{html.escape(row['signal_type'])}</td>
          <td><span class="severity {html.escape(row['severity'])}">{html.escape(row['severity'])}</span></td>
          <td>{html.escape(row['official_response_modes'] or 'no visible official reply')}</td>
          <td><a href="{html.escape(row['url'], quote=True)}">source ↗</a></td>
        </tr>"""
        for row in sorted(
            analyzed,
            key=lambda item: (
                severity_order.get(item["severity"], 9),
                -int(item["likes"] or 0),
                item["comment_id"],
            ),
        )[:30]
    )

    monthly = summary["context_month_counts"]
    assert isinstance(monthly, dict)
    month_chips = "".join(
        f"<span class='chip'><b>{html.escape(month)}</b> {count}</span>"
        for month, count in monthly.items()
    )
    sentiment = summary["sentiment_counts"]
    signal_types = summary["signal_type_counts"]
    response_modes = summary["response_mode_counts"]
    assert isinstance(sentiment, dict)
    assert isinstance(signal_types, dict)
    assert isinstance(response_modes, dict)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(summary['target']))} · Customer Voice</title>
  <style>
    :root {{ --ink:#0b2239; --gold:#d49a43; --paper:#f4f7f9; --muted:#637485; --line:#d9e2e8; --risk:#a63b32; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font:15px/1.6 Inter,ui-sans-serif,system-ui,sans-serif; }}
    a {{ color:#0e668c; text-decoration:none; }}
    header {{ background:var(--ink); color:white; padding:58px 7vw 74px; border-bottom:4px solid var(--gold); }}
    .eyebrow {{ color:#e9b45f; letter-spacing:.15em; text-transform:uppercase; font-size:12px; font-weight:800; }}
    h1 {{ max-width:920px; margin:12px 0 10px; font-size:clamp(36px,6vw,64px); line-height:1.05; }}
    header p {{ max-width:850px; color:#d7e1e8; font-size:18px; }}
    main {{ max-width:1180px; margin:auto; padding:0 24px 80px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:-46px; }}
    .metric,section {{ background:white; border:1px solid var(--line); border-radius:9px; box-shadow:0 12px 30px #09233b10; }}
    .metric {{ padding:20px; }}
    .metric b {{ display:block; font-size:29px; }}
    .metric span {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.07em; }}
    section {{ padding:28px; margin-top:20px; }}
    h2 {{ margin:0 0 6px; }}
    .sub {{ margin:0 0 20px; color:var(--muted); }}
    .chips {{ display:flex; flex-wrap:wrap; gap:9px; }}
    .chip {{ background:#edf3f6; border-radius:999px; padding:7px 12px; }}
    .note {{ margin-top:18px; padding:14px 16px; border:1px solid #ecd09e; background:#fff9ef; color:#6c4a18; border-radius:6px; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; min-width:820px; font-size:13px; }}
    th,td {{ padding:11px 9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }}
    .severity {{ padding:3px 8px; border-radius:999px; background:#edf3f6; }}
    .severity.high,.severity.critical {{ color:white; background:var(--risk); }}
    @media (max-width:760px) {{ .metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
  </style>
</head>
<body>
  <header>
    <div class="eyebrow">Public Web Census · Customer Voice Mode</div>
    <h1>{html.escape(str(summary['target']))} customer voice</h1>
    <p>Public customer signals analyzed as traceable evidence: issue, intent, sentiment, severity, and visible official response remain linked to source rows.</p>
  </header>
  <main>
    <div class="metrics">
      <div class="metric"><b>{total}</b><span>customer signals</span></div>
      <div class="metric"><b>{html.escape(str(issue_labels.get(top_issue, top_issue) or 'n/a'))}</b><span>top issue · {top_issue_count}/{total}</span></div>
      <div class="metric"><b>{summary['high_or_critical']}</b><span>high + critical</span></div>
      <div class="metric"><b>{summary['visible_response_count']}/{total}</b><span>visible official response</span></div>
    </div>

    <section>
      <h2>Issue map</h2>
      <p class="sub">Issue categories emerged from the complete customer corpus; counts retain denominators and evidence IDs.</p>
      <div class="table-wrap"><table><thead><tr><th>Issue</th><th>n/N</th><th>Share</th><th>Evidence IDs</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
    </section>

    <section>
      <h2>Signal and response patterns</h2>
      <div class="chips">
        <span class="chip"><b>Intent</b> {html.escape(', '.join(f'{key} {value}/{total}' for key, value in signal_types.items()))}</span>
        <span class="chip"><b>Sentiment</b> {html.escape(', '.join(f'{key} {value}/{total}' for key, value in sentiment.items()))}</span>
        <span class="chip"><b>Official response</b> {html.escape(', '.join(f'{key} {value}' for key, value in response_modes.items()) or 'none classified')}</span>
      </div>
      <div class="note"><b>Interpretation boundary:</b> public comments are observable signals, not a representative customer survey. High-severity records require human review before escalation.</div>
    </section>

    <section>
      <h2>Context timeline</h2>
      <p class="sub">Months use the parent content publication date when the comment timestamp is unavailable.</p>
      <div class="chips">{month_chips or '<span class="chip">No comparable dates</span>'}</div>
    </section>

    <section>
      <h2>Redacted evidence ledger</h2>
      <p class="sub">Public usernames are replaced with stable aliases in the report. Raw evidence remains local and unchanged.</p>
      <div class="table-wrap"><table><thead><tr><th>ID</th><th>Alias</th><th>Working translation</th><th>Issue</th><th>Intent</th><th>Severity</th><th>Visible response</th><th>Evidence</th></tr></thead><tbody>{evidence_rows}</tbody></table></div>
    </section>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Bundle with voice analysis files")
    parser.add_argument("--no-report", action="store_true", help="Validate and merge without HTML output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle = args.bundle.resolve()
    validation_path = bundle / "voice" / "validation_report.json"
    try:
        _comment_fields, comments, content, results, validation, labels = validate(bundle)
    except VoiceValidationError as exc:
        write_json_atomic(validation_path, exc.report)
        for error in exc.report["errors"]:
            print(f"ERROR: {error}")
        print(f"Validation report: {validation_path}")
        return 1
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    analyzed_path = bundle / "analyzed_voice.csv"
    analyzed = write_analyzed_voice(analyzed_path, comments, results)
    summary = build_summary(bundle, content, analyzed, labels)
    summary_path = bundle / "customer_voice_summary.json"
    write_json_atomic(validation_path, validation)
    write_json_atomic(summary_path, summary)
    report_path = bundle / "customer_voice_report.html"
    if not args.no_report:
        report_path.write_text(render_report(summary, analyzed, content), encoding="utf-8")

    print(f"Validated {len(analyzed)} customer signals across {len(summary['issue_counts'])} issues.")
    print(f"Analyzed voice dataset: {analyzed_path}")
    print(f"Validation report: {validation_path}")
    if not args.no_report:
        print(f"Customer voice report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
