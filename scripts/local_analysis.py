#!/usr/bin/env python3
"""Run the model-dependent analysis phase with a local Ollama model.

Collection remains deterministic and is handled by the platform connectors. This
module only fills the existing model-agnostic analysis packet, then delegates
final validation to apply_analysis.py or apply_customer_voice.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
FALLBACK_MODEL = "AlphaESS-Brain:latest"
CONTENT_FIELDS = [
    "record_id",
    "text_translation",
    "content_type",
    "classification_confidence",
    "classification_notes",
]
VOICE_FIELDS = [
    "comment_id",
    "text_translation",
    "issue_type",
    "signal_type",
    "sentiment",
    "severity",
    "analysis_confidence",
    "analysis_notes",
]
VOICE_SIGNAL_TYPES = {"question", "complaint", "request", "praise", "experience", "other"}
VOICE_SENTIMENTS = {"positive", "neutral", "negative", "mixed", "unclear"}
VOICE_SEVERITIES = {"informational", "low", "medium", "high", "critical"}
CONFIDENCES = {"high", "medium", "low"}


class LocalAgentError(RuntimeError):
    """Raised when the local model cannot produce a safe, complete packet."""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def clean_text(value: Any, limit: int = 480) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text[:limit]


def slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or fallback


def parse_json_content(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            raise LocalAgentError("Ollama returned no JSON object or array")
        start = min(starts)
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
        raise LocalAgentError("Ollama returned malformed JSON")


class OllamaClient:
    def __init__(self, host: str, model: str, timeout: int = 600, context: int = 32768) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.context = context

    def chat_json(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": self.context},
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LocalAgentError(f"Ollama request failed at {self.host}: {exc}") from exc
        message = result.get("message") if isinstance(result, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LocalAgentError("Ollama returned an empty assistant message")
        return parse_json_content(content)


def schema_taxonomy(mode: str) -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string"},
            "definition": {"type": "string"},
            "inclusion_criteria": {"type": "array", "items": {"type": "string"}},
            "exclusion_criteria": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "label", "definition", "inclusion_criteria", "exclusion_criteria"],
    }
    key = "categories" if mode == "content" else "issues"
    return {"type": "object", "properties": {key: {"type": "array", "items": item}}, "required": [key]}


def schema_content_results() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "record_id": {"type": "string"},
            "text_translation": {"type": "string"},
            "content_type": {"type": "string"},
            "classification_confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
            "classification_notes": {"type": "string"},
        },
        "required": CONTENT_FIELDS,
    }
    return {"type": "object", "properties": {"records": {"type": "array", "items": item}}, "required": ["records"]}


def schema_voice_results() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "comment_id": {"type": "string"},
            "text_translation": {"type": "string"},
            "issue_type": {"type": "string"},
            "signal_type": {"type": "string", "enum": sorted(VOICE_SIGNAL_TYPES)},
            "sentiment": {"type": "string", "enum": sorted(VOICE_SENTIMENTS)},
            "severity": {"type": "string", "enum": sorted(VOICE_SEVERITIES)},
            "analysis_confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
            "analysis_notes": {"type": "string"},
        },
        "required": VOICE_FIELDS,
    }
    return {"type": "object", "properties": {"records": {"type": "array", "items": item}}, "required": ["records"]}


def chunks(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def compact_rows(rows: list[dict[str, str]], mode: str) -> str:
    key = "record_id" if mode == "content" else "comment_id"
    return "\n".join(
        json.dumps(
            {"id": row.get(key, ""), "text": clean_text(row.get("text_original", ""), 360)},
            ensure_ascii=False,
        )
        for row in rows
    )


def taxonomy_prompt(mode: str, rows: list[dict[str, str]], target_language: str) -> tuple[str, str]:
    subject = "content purpose" if mode == "content" else "customer issue"
    key = "categories" if mode == "content" else "issues"
    system = (
        "You are a careful evidence analyst. Return only JSON matching the schema. "
        "Use repeated meanings in the supplied source rows, not a preset taxonomy. "
        "Never invent source IDs. Keep the taxonomy compact and operational."
    )
    user = f"""Derive a compact taxonomy of {subject} from this complete corpus batch.
Return an object with key {key}. Each item needs a short lowercase id, label, definition,
one or more inclusion_criteria, and one or more exclusion_criteria. Do not classify rows yet.
The working translation language is {target_language}.

Source rows (IDs and original text):
{compact_rows(rows, mode)}"""
    return system, user


def classify_prompt(mode: str, rows: list[dict[str, str]], taxonomy: list[dict[str, Any]], target_language: str) -> tuple[str, str]:
    key = "record_id" if mode == "content" else "comment_id"
    allowed_key = "categories" if mode == "content" else "issues"
    allowed = [item["id"] for item in taxonomy]
    system = (
        "You are a strict evidence classification worker. Return only JSON matching the schema. "
        "Return exactly one result for every supplied ID, with no extra IDs and no prose. "
        "Translate faithfully; do not infer facts not visible in the source text."
    )
    lines = "\n".join(
        json.dumps(
            {"id": row.get(key, ""), "text": clean_text(row.get("text_original", ""), 700)},
            ensure_ascii=False,
        )
        for row in rows
    )
    if mode == "content":
        instructions = (
            "For each record provide record_id, text_translation, content_type, "
            "classification_confidence (high/medium/low), and classification_notes."
        )
    else:
        instructions = (
            "For each comment provide comment_id, text_translation, issue_type, signal_type, "
            "sentiment, severity, analysis_confidence, and analysis_notes. "
            "Allowed signal_type: question, complaint, request, praise, experience, other. "
            "Allowed sentiment: positive, neutral, negative, mixed, unclear. "
            "Allowed severity: informational, low, medium, high, critical."
        )
    user = f"""{instructions}
Use only these taxonomy IDs for {allowed_key}: {json.dumps(allowed, ensure_ascii=False)}
The working translation language is {target_language}.

Rows:
{lines}"""
    return system, user


def normalize_taxonomy(raw: Any, mode: str) -> list[dict[str, Any]]:
    key = "categories" if mode == "content" else "issues"
    items = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise LocalAgentError(f"Local taxonomy response must contain {key}")
    output: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        label = clean_text(item.get("label"), 120)
        raw_id = clean_text(item.get("id"), 80)
        item_id = slugify(raw_id or label, f"category_{index}")
        base = item_id
        suffix = 2
        while item_id in used:
            item_id = f"{base}_{suffix}"
            suffix += 1
        used.add(item_id)
        inclusion = item.get("inclusion_criteria")
        exclusion = item.get("exclusion_criteria")
        output.append(
            {
                "id": item_id,
                "label": label or item_id.replace("_", " ").title(),
                "definition": clean_text(item.get("definition"), 400) or f"Records primarily about {label or item_id}.",
                "inclusion_criteria": [clean_text(value, 240) for value in inclusion if clean_text(value, 240)]
                if isinstance(inclusion, list)
                else [],
                "exclusion_criteria": [clean_text(value, 240) for value in exclusion if clean_text(value, 240)]
                if isinstance(exclusion, list)
                else [],
            }
        )
    if not output:
        raise LocalAgentError("Local taxonomy response contained no usable categories")
    for item in output:
        if not item["inclusion_criteria"]:
            item["inclusion_criteria"] = [f"The primary visible meaning is {item['label'] }."]
        if not item["exclusion_criteria"]:
            item["exclusion_criteria"] = ["The topic is only incidental or cannot be supported by the visible text."]
    return output


def merge_taxonomies(client: OllamaClient, mode: str, proposals: list[dict[str, Any]], target_language: str) -> list[dict[str, Any]]:
    key = "categories" if mode == "content" else "issues"
    current = proposals
    while len(current) > 1:
        merged: list[dict[str, Any]] = []
        for proposal_group in chunks(current, 6):
            if len(proposal_group) == 1:
                merged.append(proposal_group[0])
                continue
            system = "You consolidate evidence taxonomies. Return only JSON matching the schema; do not add prose."
            user = f"""Consolidate these batch taxonomy proposals into a compact taxonomy.
Keep only recurring, decision-useful distinctions; merge synonyms. Return key {key}.
Every item needs a lowercase id, label, definition, inclusion_criteria, and exclusion_criteria.
Working language: {target_language}

Proposals:
{json.dumps(proposal_group, ensure_ascii=False)}"""
            merged.append(client.chat_json(system, user, schema_taxonomy(mode)))
        current = merged
    return normalize_taxonomy(current[0], mode)


def call_with_retry(
    client: OllamaClient,
    mode: str,
    rows: list[dict[str, str]],
    taxonomy: list[dict[str, Any]],
    target_language: str,
) -> list[dict[str, str]]:
    system, user = classify_prompt(mode, rows, taxonomy, target_language)
    raw = client.chat_json(system, user, schema_content_results() if mode == "content" else schema_voice_results())
    result_rows = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(result_rows, list):
        raise LocalAgentError("Local classification response must contain records")
    key = "record_id" if mode == "content" else "comment_id"
    expected = {row.get(key, "").strip() for row in rows}
    by_id = {str(row.get(key, "")).strip(): row for row in result_rows if isinstance(row, dict)}
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        unknown = sorted(set(by_id) - expected)
        retry_user = user + f"\nCorrection: missing IDs={missing}; unknown IDs={unknown}. Return exactly the listed IDs."
        raw = client.chat_json(system, retry_user, schema_content_results() if mode == "content" else schema_voice_results())
        result_rows = raw.get("records") if isinstance(raw, dict) else None
        by_id = {str(row.get(key, "")).strip(): row for row in result_rows or [] if isinstance(row, dict)}
    if set(by_id) != expected:
        raise LocalAgentError(f"Local model did not return exactly one result per ID: expected {len(expected)}, got {len(by_id)}")
    return [by_id[row[key].strip()] for row in rows]


def add_representatives(taxonomy: list[dict[str, Any]], rows: list[dict[str, str]], mode: str) -> list[dict[str, Any]]:
    key = "record_id" if mode == "content" else "comment_id"
    category_key = "content_type" if mode == "content" else "issue_type"
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row.get(category_key, "").strip()].append(row.get(key, "").strip())
    output = []
    for item in taxonomy:
        ids = grouped.get(item["id"], [])
        if not ids:
            continue
        updated = dict(item)
        field = "representative_record_ids" if mode == "content" else "representative_comment_ids"
        updated[field] = ids[:3]
        output.append(updated)
    if not output:
        raise LocalAgentError("No classified row matched the local taxonomy")
    return output


def write_taxonomy(path: Path, taxonomy: list[dict[str, Any]], mode: str, target_language: str) -> None:
    key = "categories" if mode == "content" else "issues"
    schema_version = "0.3" if mode == "content" else "0.4"
    value = {
        "schema_version": schema_version,
        "target_language": target_language,
        "derivation_notes": "Categories were proposed from the complete corpus in local Ollama batches and retained only where final classifications used them.",
        key: taxonomy,
    }
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_result_rows(rows: list[dict[str, str]], mode: str, taxonomy: list[dict[str, Any]]) -> list[dict[str, str]]:
    allowed = {item["id"] for item in taxonomy}
    key = "record_id" if mode == "content" else "comment_id"
    category_key = "content_type" if mode == "content" else "issue_type"
    output: list[dict[str, str]] = []
    for row in rows:
        item = {field: clean_text(row.get(field), 1200) for field in (CONTENT_FIELDS if mode == "content" else VOICE_FIELDS)}
        item[key] = clean_text(row.get(key), 200)
        if item[category_key] not in allowed:
            raise LocalAgentError(f"Local model returned unknown {category_key}: {item[category_key]}")
        confidence_key = "classification_confidence" if mode == "content" else "analysis_confidence"
        confidence = item[confidence_key].lower().strip()
        if confidence not in CONFIDENCES:
            try:
                score = float(confidence)
                confidence = "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"
            except ValueError:
                confidence = "low"
        item[confidence_key] = confidence
        if mode == "content":
            if not item["text_translation"]:
                raise LocalAgentError(f"Local model returned blank translation for {item[key]}")
        else:
            if not item["text_translation"]:
                raise LocalAgentError(f"Local model returned blank translation for {item[key]}")
            for field, allowed_values in (
                ("signal_type", VOICE_SIGNAL_TYPES),
                ("sentiment", VOICE_SENTIMENTS),
                ("severity", VOICE_SEVERITIES),
            ):
                item[field] = item[field].lower().strip()
                if item[field] not in allowed_values:
                    raise LocalAgentError(f"Local model returned invalid {field}: {item[field]}")
        output.append(item)
    return output


def choose_model(requested: str | None, host: str) -> str:
    if requested:
        return requested
    env_model = os.environ.get("PUBLIC_WEB_CENSUS_OLLAMA_MODEL", "").strip()
    if env_model:
        return env_model
    try:
        request = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
        with urllib.request.urlopen(request, timeout=5) as response:
            models = json.loads(response.read().decode("utf-8")).get("models", [])
        names = {item.get("name") for item in models if isinstance(item, dict)}
        if DEFAULT_MODEL in names:
            return DEFAULT_MODEL
        if FALLBACK_MODEL in names:
            return FALLBACK_MODEL
    except Exception:
        pass
    return DEFAULT_MODEL


def prepare_packet(bundle: Path, mode: str, target_language: str, force: bool) -> Path:
    script = ROOT / "scripts" / ("prepare_analysis.py" if mode == "content" else "prepare_customer_voice.py")
    args = [sys.executable, str(script), "--bundle", str(bundle), "--target-language", target_language]
    if force:
        args.append("--force")
    subprocess.run(args, cwd=ROOT, check=True)
    return bundle / ("analysis" if mode == "content" else "voice")


def run(args: argparse.Namespace) -> int:
    bundle = args.bundle.resolve()
    mode = args.mode
    target_language = args.target_language.strip() or "English"
    packet = prepare_packet(bundle, mode, target_language, args.force)
    source_path = bundle / "content.csv"
    source_rows = read_csv(source_path)
    if mode == "customer-voice":
        source_rows = [row for row in read_csv(bundle / "comments.csv") if row.get("is_official", "").strip().lower() != "true"]
        model_mode = "voice"
    else:
        model_mode = "content"
    model = choose_model(args.model, args.host)
    client = OllamaClient(args.host, model, args.timeout, args.context)
    print(f"Local Ollama model: {model}", file=sys.stderr)
    proposals: list[dict[str, Any]] = []
    for index, batch in enumerate(chunks(source_rows, args.taxonomy_batch_size), start=1):
        system, user = taxonomy_prompt(model_mode, batch, target_language)
        proposals.append(client.chat_json(system, user, schema_taxonomy(model_mode)))
        print(f"Taxonomy batch {index}/{(len(source_rows) + args.taxonomy_batch_size - 1) // args.taxonomy_batch_size}", file=sys.stderr)
    taxonomy = merge_taxonomies(client, model_mode, proposals, target_language)
    results: list[dict[str, str]] = []
    for index, batch in enumerate(chunks(source_rows, args.batch_size), start=1):
        results.extend(call_with_retry(client, model_mode, batch, taxonomy, target_language))
        print(f"Classification batch {index}/{(len(source_rows) + args.batch_size - 1) // args.batch_size}", file=sys.stderr)
    normalized = normalize_result_rows(results, model_mode, taxonomy)
    taxonomy = add_representatives(taxonomy, normalized, model_mode)
    if mode == "content":
        write_taxonomy(packet / "taxonomy.json", taxonomy, "content", target_language)
        write_csv(packet / "analysis_results.csv", CONTENT_FIELDS, normalized)
        apply_script = ROOT / "scripts" / "apply_analysis.py"
    else:
        write_taxonomy(packet / "voice_taxonomy.json", taxonomy, "customer-voice", target_language)
        write_csv(packet / "voice_results.csv", VOICE_FIELDS, normalized)
        apply_script = ROOT / "scripts" / "apply_customer_voice.py"
    apply_args = [sys.executable, str(apply_script), "--bundle", str(bundle)]
    if args.no_report:
        apply_args.append("--no-report")
    subprocess.run(apply_args, cwd=ROOT, check=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Evidence bundle containing content.csv and comments.csv")
    parser.add_argument("--mode", choices=["content", "customer-voice"], default="content")
    parser.add_argument("--model", help=f"Ollama model (default: auto-select {DEFAULT_MODEL}, fallback {FALLBACK_MODEL})")
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT_HOST))
    parser.add_argument("--target-language", default="English")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--taxonomy-batch-size", type=int, default=30)
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--force", action="store_true", help="Replace an existing analysis packet")
    parser.add_argument("--no-report", action="store_true", help="Skip HTML report rendering after validation")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.batch_size < 1 or args.taxonomy_batch_size < 1:
            raise LocalAgentError("batch sizes must be positive")
        return run(args)
    except (LocalAgentError, subprocess.CalledProcessError) as exc:
        print(f"local analysis failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
