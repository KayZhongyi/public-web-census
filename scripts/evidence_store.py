#!/usr/bin/env python3
"""Manage a versioned SQLite ledger for Public Web Census evidence bundles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
DATABASE_NAME = "evidence.sqlite3"
PROJECT_NAME = "project.json"
BUNDLE_FILES = ("platform_census.csv", "content.csv", "comments.csv", "run_manifest.json")
TABLE_FILES = {
    "platform": "platform_census.csv",
    "content": "content.csv",
    "comment": "comments.csv",
}
ID_FIELDS = {"platform": "", "content": "record_id", "comment": "comment_id"}
CORE_FIELDS = {
    "platform": [
        "platform",
        "handle",
        "url",
        "identity_status",
        "identity_evidence",
        "followers",
        "visible_items",
        "last_active_at",
        "deep_dive",
        "notes",
    ],
    "content": [
        "record_id",
        "platform",
        "account",
        "published_at",
        "language",
        "text_original",
        "text_translation",
        "views",
        "likes",
        "comments_count",
        "shares",
        "url",
        "content_type",
        "brand",
        "collected_at",
        "retrieval_status",
    ],
    "comment": [
        "comment_id",
        "content_id",
        "parent_comment_id",
        "commenter",
        "commenter_type",
        "is_official",
        "text_original",
        "text_translation",
        "published_at",
        "likes",
        "topic",
        "response_mode",
        "url",
    ],
}
IGNORED_CHANGE_FIELDS = {"collected_at"}
CONTENT_CHANGE_FIELDS = {
    "text_original",
    "text_translation",
    "content_type",
    "topic",
    "brand",
    "media_type",
}
ENGAGEMENT_FIELDS = {
    "views",
    "likes",
    "comments_count",
    "shares",
    "followers",
    "visible_items",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_csv_atomic(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    return {str(field): "" if value is None else str(value) for field, value in row.items()}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        return fields, [normalize_row(row) for row in reader]


def bundle_fingerprint(bundle: Path) -> str:
    digest = hashlib.sha256()
    for filename in BUNDLE_FILES:
        path = bundle / filename
        if not path.is_file():
            raise ValueError(f"Missing required evidence-bundle file: {path}")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_manifest(bundle: Path) -> dict[str, Any]:
    try:
        value = json.loads((bundle / "run_manifest.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {bundle / 'run_manifest.json'}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("run_manifest.json must contain a JSON object")
    return value


def stable_id(entity_type: str, row: dict[str, str]) -> str:
    id_field = ID_FIELDS[entity_type]
    if id_field:
        return row.get(id_field, "").strip()
    platform = row.get("platform", "").strip().casefold()
    handle = row.get("handle", "").strip().casefold()
    url = row.get("url", "").strip().rstrip("/").casefold()
    identity = canonical_json({"platform": platform, "handle": handle, "url": url})
    return f"platform-{sha256_text(identity)[:20]}" if platform and (handle or url) else ""


def validate_table(
    path: Path, entity_type: str, fields: list[str], rows: list[dict[str, str]]
) -> None:
    if not fields:
        raise ValueError(f"{path}: missing CSV header")
    id_field = ID_FIELDS[entity_type]
    if id_field and id_field not in fields:
        raise ValueError(f"{path}: missing stable ID field {id_field}")
    ids = [stable_id(entity_type, row) for row in rows]
    if any(not value for value in ids):
        raise ValueError(f"{path}: contains a blank or unresolvable stable ID")
    duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"{path}: duplicate stable IDs: {', '.join(duplicates[:10])}")


def read_bundle(
    bundle: Path,
) -> tuple[dict[str, tuple[list[str], list[dict[str, str]]]], dict[str, Any], str]:
    bundle = bundle.resolve()
    fingerprint = bundle_fingerprint(bundle)
    manifest = load_manifest(bundle)
    if manifest.get("artifact_type") == "derived_current_snapshot":
        raise ValueError("A derived current snapshot cannot be imported as a new source run")
    tables: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    for entity_type, filename in TABLE_FILES.items():
        fields, rows = read_csv(bundle / filename)
        validate_table(bundle / filename, entity_type, fields, rows)
        tables[entity_type] = (fields, rows)
    return tables, manifest, fingerprint


def derive_observed_at(manifest: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit
    for field in ("cutoff_utc", "collected_at", "started_at_utc", "generated_at"):
        value = manifest.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return utc_now()


def derive_scope(
    manifest: dict[str, Any], tables: dict[str, tuple[list[str], list[dict[str, str]]]], explicit: str | None
) -> tuple[str, dict[str, Any]]:
    if explicit:
        return explicit, {"declared_scope_key": explicit}
    sources: set[tuple[str, str, str]] = set()
    for row in tables["platform"][1]:
        platform = row.get("platform", "").strip()
        account = row.get("handle", "").strip()
        url = row.get("url", "").strip()
        if platform or account or url:
            sources.add((platform, account, url))
    payload = {
        "target": manifest.get("target", {}),
        "input_url": (
            manifest.get("normalized_channel_url")
            or manifest.get("normalized_profile_url")
            or manifest.get("normalized_page_url")
            or manifest.get("input_url", "")
        ),
        "sources": [list(item) for item in sorted(sources)],
        "selected_tabs": manifest.get("selected_tabs", []),
    }
    return f"scope-{sha256_text(canonical_json(payload))[:16]}", payload


def make_run_id(observed_at: str, fingerprint: str) -> str:
    stamp = "".join(character for character in observed_at if character.isdigit())[:14]
    return f"run-{stamp or 'undated'}-{fingerprint[:12]}"


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                label TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                bundle_fingerprint TEXT NOT NULL UNIQUE,
                archived_bundle TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                table_schemas_json TEXT NOT NULL,
                counts_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                entity_type TEXT NOT NULL,
                stable_id TEXT NOT NULL,
                first_seen_run INTEGER NOT NULL REFERENCES runs(run_sequence),
                last_seen_run INTEGER NOT NULL REFERENCES runs(run_sequence),
                PRIMARY KEY (entity_type, stable_id)
            );

            CREATE TABLE IF NOT EXISTS observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_sequence INTEGER NOT NULL REFERENCES runs(run_sequence) ON DELETE RESTRICT,
                entity_type TEXT NOT NULL,
                stable_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                platform TEXT NOT NULL,
                account TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                row_json TEXT NOT NULL,
                UNIQUE (run_sequence, entity_type, stable_id),
                FOREIGN KEY (entity_type, stable_id) REFERENCES entities(entity_type, stable_id)
            );

            CREATE TABLE IF NOT EXISTS changes (
                change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_sequence INTEGER NOT NULL REFERENCES runs(run_sequence) ON DELETE RESTRICT,
                entity_type TEXT NOT NULL,
                stable_id TEXT NOT NULL,
                change_type TEXT NOT NULL,
                previous_observation_id INTEGER REFERENCES observations(observation_id),
                current_observation_id INTEGER REFERENCES observations(observation_id),
                changed_fields_json TEXT NOT NULL,
                change_kinds_json TEXT NOT NULL,
                field_changes_json TEXT NOT NULL,
                UNIQUE (run_sequence, entity_type, stable_id)
            );

            CREATE INDEX IF NOT EXISTS observations_entity_idx
                ON observations(entity_type, stable_id, observation_id);
            CREATE INDEX IF NOT EXISTS runs_scope_idx
                ON runs(scope_key, run_sequence);
            CREATE INDEX IF NOT EXISTS changes_run_idx
                ON changes(run_sequence, change_type);
            """
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCHEMA_VERSION,),
        )


def workspace_paths(workspace: Path) -> tuple[Path, Path]:
    workspace = workspace.resolve()
    return workspace / PROJECT_NAME, workspace / DATABASE_NAME


def initialize_workspace(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    project_path, database = workspace_paths(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for directory in ("captures", "changes", "current", "discovery"):
        (workspace / directory).mkdir(exist_ok=True)
    project = {
        "schema_version": SCHEMA_VERSION,
        "target": args.target.strip(),
        "market": args.market.strip(),
        "research_mode": args.mode.strip(),
        "purpose": args.purpose.strip(),
        "owner": args.owner.strip(),
        "created_at": utc_now(),
        "public_source_boundary": "authorized, ordinarily visible public web sources",
    }
    if project_path.exists():
        existing = json.loads(project_path.read_text(encoding="utf-8"))
        if existing.get("target") != project["target"]:
            raise ValueError(
                f"Workspace already belongs to target {existing.get('target')!r}; never mix targets"
            )
        project = existing
    else:
        write_json_atomic(project_path, project)
    initialize_database(database)
    discovery = workspace / "discovery" / "platform_census.csv"
    if not discovery.exists():
        write_csv_atomic(discovery, CORE_FIELDS["platform"], [])
    return project


def require_workspace(workspace: Path) -> tuple[dict[str, Any], Path]:
    project_path, database = workspace_paths(workspace)
    if not project_path.is_file() or not database.is_file():
        raise ValueError(
            f"Workspace is not initialized: {workspace}. Run public-web-census discover first."
        )
    project = json.loads(project_path.read_text(encoding="utf-8"))
    initialize_database(database)
    return project, database


def changed_fields(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    fields = sorted(set(previous) | set(current))
    return [
        field
        for field in fields
        if field not in IGNORED_CHANGE_FIELDS and previous.get(field, "") != current.get(field, "")
    ]


def change_kinds(fields: Iterable[str]) -> list[str]:
    fields = set(fields)
    kinds: list[str] = []
    if fields & CONTENT_CHANGE_FIELDS:
        kinds.append("content")
    if fields & ENGAGEMENT_FIELDS:
        kinds.append("engagement")
    if fields - CONTENT_CHANGE_FIELDS - ENGAGEMENT_FIELDS:
        kinds.append("metadata")
    return kinds or ["none"]


def archive_bundle(bundle: Path, workspace: Path, run_id: str) -> tuple[Path, bool]:
    destination = workspace / "captures" / run_id
    if destination.exists():
        if bundle_fingerprint(destination) != bundle_fingerprint(bundle):
            raise ValueError(f"Archive destination already exists with different content: {destination}")
        return destination, False
    temporary = workspace / "captures" / f".{run_id}.pending"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        for filename in BUNDLE_FILES:
            shutil.copy2(bundle / filename, temporary / filename)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, True


def latest_observation(
    connection: sqlite3.Connection, entity_type: str, record_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT observation_id, row_hash, row_json FROM observations "
        "WHERE entity_type=? AND stable_id=? ORDER BY observation_id DESC LIMIT 1",
        (entity_type, record_id),
    ).fetchone()


def previous_scope_ids(
    connection: sqlite3.Connection, previous_run: int | None, entity_type: str
) -> dict[str, int]:
    if previous_run is None:
        return {}
    return {
        row["stable_id"]: row["observation_id"]
        for row in connection.execute(
            "SELECT stable_id, observation_id FROM observations "
            "WHERE run_sequence=? AND entity_type=?",
            (previous_run, entity_type),
        )
    }


def insert_change(
    connection: sqlite3.Connection,
    run_sequence: int,
    entity_type: str,
    record_id: str,
    change_type: str,
    previous_id: int | None,
    current_id: int | None,
    fields: list[str],
    field_changes: dict[str, dict[str, str]],
) -> None:
    connection.execute(
        "INSERT INTO changes(run_sequence, entity_type, stable_id, change_type, "
        "previous_observation_id, current_observation_id, changed_fields_json, "
        "change_kinds_json, field_changes_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            run_sequence,
            entity_type,
            record_id,
            change_type,
            previous_id,
            current_id,
            canonical_json(fields),
            canonical_json(change_kinds(fields) if change_type == "updated" else []),
            canonical_json(field_changes),
        ),
    )


def report_for_run(connection: sqlite3.Connection, run_sequence: int) -> dict[str, Any]:
    run = connection.execute("SELECT * FROM runs WHERE run_sequence=?", (run_sequence,)).fetchone()
    if run is None:
        raise ValueError(f"Unknown run sequence: {run_sequence}")
    rows = connection.execute(
        "SELECT entity_type, stable_id, change_type, changed_fields_json, "
        "change_kinds_json, field_changes_json FROM changes "
        "WHERE run_sequence=? ORDER BY entity_type, change_type, stable_id",
        (run_sequence,),
    ).fetchall()
    counts = Counter(row["change_type"] for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run["run_id"],
        "observed_at": run["observed_at"],
        "scope_key": run["scope_key"],
        "counts": {
            key: counts.get(key, 0)
            for key in ("new", "updated", "unchanged", "not_observed")
        },
        "changes": [
            {
                "entity_type": row["entity_type"],
                "stable_id": row["stable_id"],
                "change_type": row["change_type"],
                "changed_fields": json.loads(row["changed_fields_json"]),
                "change_kinds": json.loads(row["change_kinds_json"]),
                "field_changes": json.loads(row["field_changes_json"]),
            }
            for row in rows
        ],
        "note": (
            "not_observed means the record was absent from this same-scope capture. "
            "It is retained in the ledger and is not treated as deleted."
        ),
    }


def ingest_bundle(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    bundle = args.bundle.resolve()
    _, database = require_workspace(workspace)
    tables, manifest, fingerprint = read_bundle(bundle)
    observed_at = derive_observed_at(manifest, args.observed_at)
    scope_key, scope = derive_scope(manifest, tables, args.scope_key)
    run_id = make_run_id(observed_at, fingerprint)

    with connect(database) as connection:
        duplicate = connection.execute(
            "SELECT run_sequence, run_id FROM runs WHERE bundle_fingerprint=?", (fingerprint,)
        ).fetchone()
        if duplicate is not None:
            report = report_for_run(connection, duplicate["run_sequence"])
            report["duplicate_import"] = True
            return report

    archive, created_archive = archive_bundle(bundle, workspace, run_id)
    try:
        with connect(database) as connection:
            previous = connection.execute(
                "SELECT run_sequence FROM runs WHERE scope_key=? "
                "ORDER BY run_sequence DESC LIMIT 1",
                (scope_key,),
            ).fetchone()
            previous_run = previous["run_sequence"] if previous else None
            schemas = {entity: fields for entity, (fields, _) in tables.items()}
            counts = {entity: len(rows) for entity, (_, rows) in tables.items()}
            cursor = connection.execute(
                "INSERT INTO runs(run_id, imported_at, observed_at, label, scope_key, scope_json, "
                "bundle_fingerprint, archived_bundle, manifest_json, table_schemas_json, counts_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    utc_now(),
                    observed_at,
                    (args.label or run_id).strip(),
                    scope_key,
                    canonical_json(scope),
                    fingerprint,
                    str(archive.relative_to(workspace)),
                    canonical_json(manifest),
                    canonical_json(schemas),
                    canonical_json(counts),
                ),
            )
            run_sequence = int(cursor.lastrowid)

            for entity_type, (_, rows) in tables.items():
                incoming_ids: set[str] = set()
                for row in rows:
                    record_id = stable_id(entity_type, row)
                    incoming_ids.add(record_id)
                    previous_observation = latest_observation(connection, entity_type, record_id)
                    previous_row = (
                        json.loads(previous_observation["row_json"]) if previous_observation else {}
                    )
                    fields = changed_fields(previous_row, row) if previous_observation else []
                    row_json = canonical_json(row)
                    row_hash = sha256_text(row_json)

                    connection.execute(
                        "INSERT INTO entities(entity_type, stable_id, first_seen_run, last_seen_run) "
                        "VALUES(?,?,?,?) ON CONFLICT(entity_type, stable_id) DO UPDATE SET "
                        "last_seen_run=excluded.last_seen_run",
                        (entity_type, record_id, run_sequence, run_sequence),
                    )
                    observation_cursor = connection.execute(
                        "INSERT INTO observations(run_sequence, entity_type, stable_id, observed_at, "
                        "platform, account, canonical_url, row_hash, row_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            run_sequence,
                            entity_type,
                            record_id,
                            observed_at,
                            row.get("platform", ""),
                            row.get("account", row.get("handle", "")),
                            row.get("url", ""),
                            row_hash,
                            row_json,
                        ),
                    )
                    observation_id = int(observation_cursor.lastrowid)
                    if previous_observation is None:
                        change_type = "new"
                    elif fields:
                        change_type = "updated"
                    else:
                        change_type = "unchanged"
                    field_changes = {
                        field: {"before": previous_row.get(field, ""), "after": row.get(field, "")}
                        for field in fields
                    }
                    insert_change(
                        connection,
                        run_sequence,
                        entity_type,
                        record_id,
                        change_type,
                        previous_observation["observation_id"] if previous_observation else None,
                        observation_id,
                        fields,
                        field_changes,
                    )

                if rows:
                    for record_id, observation_id in previous_scope_ids(
                        connection, previous_run, entity_type
                    ).items():
                        if record_id not in incoming_ids:
                            insert_change(
                                connection,
                                run_sequence,
                                entity_type,
                                record_id,
                                "not_observed",
                                observation_id,
                                None,
                                [],
                                {},
                            )
            report = report_for_run(connection, run_sequence)
    except Exception:
        if created_archive:
            shutil.rmtree(archive, ignore_errors=True)
        raise

    write_json_atomic(workspace / "changes" / f"{run_id}.json", report)
    if not args.no_export:
        export_current(workspace, workspace / "current")
    return report


def current_rows(connection: sqlite3.Connection, entity_type: str) -> list[dict[str, str]]:
    rows = connection.execute(
        "SELECT o.stable_id, o.row_json FROM observations o "
        "JOIN (SELECT entity_type, stable_id, MAX(observation_id) AS observation_id "
        "FROM observations WHERE entity_type=? GROUP BY entity_type, stable_id) latest "
        "ON latest.observation_id=o.observation_id ORDER BY o.stable_id",
        (entity_type,),
    ).fetchall()
    return [normalize_row(json.loads(row["row_json"])) for row in rows]


def output_fields(entity_type: str, rows: list[dict[str, str]]) -> list[str]:
    observed = {field for row in rows for field in row}
    core = list(CORE_FIELDS[entity_type])
    return core + sorted(observed - set(core))


def export_current(workspace: Path, output: Path) -> dict[str, Any]:
    project, database = require_workspace(workspace)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with connect(database) as connection:
        counts: dict[str, int] = {}
        for entity_type, filename in TABLE_FILES.items():
            rows = current_rows(connection, entity_type)
            counts[entity_type] = len(rows)
            write_csv_atomic(output / filename, output_fields(entity_type, rows), rows)
        runs = [
            {
                "run_id": row["run_id"],
                "observed_at": row["observed_at"],
                "scope_key": row["scope_key"],
                "bundle_fingerprint": row["bundle_fingerprint"],
            }
            for row in connection.execute(
                "SELECT run_id, observed_at, scope_key, bundle_fingerprint "
                "FROM runs ORDER BY run_sequence"
            )
        ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "derived_current_snapshot",
        "generated_at": utc_now(),
        "project": project,
        "counts": counts,
        "source_runs": runs,
        "note": (
            "This CSV bundle is a materialized current view. The SQLite ledger and archived "
            "source bundles remain the history and provenance record."
        ),
    }
    write_json_atomic(output / "run_manifest.json", manifest)
    return manifest


def select_run(connection: sqlite3.Connection, run_id: str | None) -> sqlite3.Row:
    if run_id:
        run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    else:
        run = connection.execute("SELECT * FROM runs ORDER BY run_sequence DESC LIMIT 1").fetchone()
    if run is None:
        raise ValueError("No imported evidence run was found")
    return run


def validate_workspace(workspace: Path) -> dict[str, Any]:
    project, database = require_workspace(workspace)
    errors: list[str] = []
    checks: dict[str, Any] = {"project_target": project.get("target", "")}
    with connect(database) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        checks["sqlite_integrity"] = integrity
        if integrity != "ok":
            errors.append(f"SQLite integrity check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        checks["foreign_key_errors"] = len(foreign_keys)
        if foreign_keys:
            errors.append(f"SQLite foreign-key check found {len(foreign_keys)} error(s)")
        bad_hashes = 0
        for row in connection.execute("SELECT observation_id, row_hash, row_json FROM observations"):
            if sha256_text(row["row_json"]) != row["row_hash"]:
                bad_hashes += 1
        checks["observation_hash_errors"] = bad_hashes
        if bad_hashes:
            errors.append(f"Observation hash check found {bad_hashes} mismatch(es)")
        run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        observation_count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        checks["runs"] = run_count
        checks["observations"] = observation_count
        archive_errors = 0
        for run in connection.execute("SELECT run_id, archived_bundle, bundle_fingerprint FROM runs"):
            archive = workspace / run["archived_bundle"]
            try:
                actual = bundle_fingerprint(archive)
            except (FileNotFoundError, ValueError):
                actual = ""
            if actual != run["bundle_fingerprint"]:
                archive_errors += 1
        checks["archive_fingerprint_errors"] = archive_errors
        if archive_errors:
            errors.append(f"Archived bundle check found {archive_errors} mismatch(es)")
    report = {
        "schema_version": SCHEMA_VERSION,
        "validated_at": utc_now(),
        "valid": not errors,
        "checks": checks,
        "errors": errors,
    }
    write_json_atomic(workspace / "current" / "validation_report.json", report)
    return report


def history_for_record(
    connection: sqlite3.Connection, entity_type: str, record_id: str
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT o.observation_id, o.observed_at, o.row_hash, o.row_json, r.run_id, r.label "
        "FROM observations o JOIN runs r ON r.run_sequence=o.run_sequence "
        "WHERE o.entity_type=? AND o.stable_id=? ORDER BY o.observation_id",
        (entity_type, record_id),
    ).fetchall()
    if not rows:
        raise ValueError(f"No history found for {entity_type}:{record_id}")
    return {
        "schema_version": SCHEMA_VERSION,
        "entity_type": entity_type,
        "stable_id": record_id,
        "observations": [
            {
                "run_id": row["run_id"],
                "label": row["label"],
                "observed_at": row["observed_at"],
                "row_hash": row["row_hash"],
                "row": json.loads(row["row_json"]),
            }
            for row in rows
        ],
    }


def add_workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, required=True, help="Versioned evidence workspace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init", help="Create a research contract and evidence ledger")
    add_workspace_argument(initialize)
    initialize.add_argument("--target", required=True, help="Company, brand, product, account, or topic")
    initialize.add_argument("--market", default="", help="Market or geography")
    initialize.add_argument("--mode", default="general", help="Declared research mode")
    initialize.add_argument("--purpose", default="", help="Business question or decision")
    initialize.add_argument("--owner", default="", help="Business owner")

    ingest = subparsers.add_parser("ingest", help="Archive and ingest a new evidence bundle")
    add_workspace_argument(ingest)
    ingest.add_argument("--bundle", type=Path, required=True, help="CSV/JSON evidence bundle")
    ingest.add_argument("--label", help="Human-readable run label")
    ingest.add_argument("--scope-key", help="Explicit repeatable collection scope")
    ingest.add_argument("--observed-at", help="ISO timestamp overriding the manifest cutoff")
    ingest.add_argument("--no-export", action="store_true", help="Do not refresh current CSV views")

    diff = subparsers.add_parser("diff", help="Show changes recorded for an imported run")
    add_workspace_argument(diff)
    diff.add_argument("--run", help="Run ID; defaults to latest")
    diff.add_argument("--output", type=Path, help="Write JSON report instead of stdout")

    export = subparsers.add_parser("export", help="Export the current materialized CSV bundle")
    add_workspace_argument(export)
    export.add_argument("--output", type=Path, help="Output directory; defaults to workspace/current")

    validate = subparsers.add_parser("validate", help="Validate ledger, hashes, archives, and references")
    add_workspace_argument(validate)

    history = subparsers.add_parser("history", help="Show every observation for one stable ID")
    add_workspace_argument(history)
    history.add_argument("--type", choices=sorted(TABLE_FILES), required=True, dest="entity_type")
    history.add_argument("--id", required=True, dest="record_id")
    history.add_argument("--output", type=Path, help="Write JSON instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            project = initialize_workspace(args)
            print(f"Workspace: {args.workspace.resolve()}")
            print(f"Target: {project['target']}")
            print(f"Discovery template: {args.workspace.resolve() / 'discovery/platform_census.csv'}")
            return 0
        if args.command == "ingest":
            report = ingest_bundle(args)
            counts = report["counts"]
            print(
                f"Run {report['run_id']}: {counts['new']} new, {counts['updated']} updated, "
                f"{counts['unchanged']} unchanged, {counts['not_observed']} not observed."
            )
            if report.get("duplicate_import"):
                print("Bundle was already imported; no new observations were written.")
            else:
                print(f"Change report: {args.workspace.resolve() / 'changes' / (report['run_id'] + '.json')}")
            return 0
        if args.command == "diff":
            _, database = require_workspace(args.workspace.resolve())
            with connect(database) as connection:
                run = select_run(connection, args.run)
                report = report_for_run(connection, run["run_sequence"])
            if args.output:
                write_json_atomic(args.output.resolve(), report)
                print(f"Change report: {args.output.resolve()}")
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "export":
            output = args.output.resolve() if args.output else args.workspace.resolve() / "current"
            manifest = export_current(args.workspace.resolve(), output)
            print(f"Current snapshot: {output}")
            print(f"Counts: {manifest['counts']}")
            return 0
        if args.command == "validate":
            report = validate_workspace(args.workspace.resolve())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 1
        if args.command == "history":
            _, database = require_workspace(args.workspace.resolve())
            with connect(database) as connection:
                report = history_for_record(connection, args.entity_type, args.record_id)
            if args.output:
                write_json_atomic(args.output.resolve(), report)
                print(f"History: {args.output.resolve()}")
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
