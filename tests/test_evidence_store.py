from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "public-web-census"
CONTENT_FIELDS = [
    "record_id",
    "platform",
    "account",
    "text_original",
    "views",
    "url",
    "collected_at",
]
PLATFORM_FIELDS = [
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
]
COMMENT_FIELDS = [
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
]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_bundle(
    root: Path,
    name: str,
    observed_at: str,
    rows: list[dict[str, str]],
) -> Path:
    bundle = root / name
    bundle.mkdir()
    write_csv(bundle / "content.csv", CONTENT_FIELDS, rows)
    write_csv(bundle / "comments.csv", COMMENT_FIELDS, [])
    write_csv(
        bundle / "platform_census.csv",
        PLATFORM_FIELDS,
        [
            {
                "platform": "YouTube",
                "handle": "@example",
                "url": "https://www.youtube.com/@example",
                "identity_status": "verified",
                "identity_evidence": "official website link",
                "followers": "100",
                "visible_items": str(len(rows)),
                "last_active_at": "2026-08-01",
                "deep_dive": "yes",
                "notes": "test scope",
            }
        ],
    )
    (bundle / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "test",
                "target": {"company": "Example", "platform": "YouTube"},
                "normalized_channel_url": "https://www.youtube.com/@example",
                "selected_tabs": ["videos"],
                "cutoff_utc": observed_at,
            }
        ),
        encoding="utf-8",
    )
    return bundle


def content(record_id: str, text: str, views: str, collected_at: str) -> dict[str, str]:
    return {
        "record_id": record_id,
        "platform": "YouTube",
        "account": "@example",
        "text_original": text,
        "views": views,
        "url": f"https://www.youtube.com/watch?v={record_id}",
        "collected_at": collected_at,
    }


class EvidenceStoreTest(unittest.TestCase):
    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=ROOT,
            check=check,
            capture_output=True,
            text=True,
        )

    def test_versioned_refresh_diff_snapshot_history_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            self.run_cli(
                "discover",
                "--workspace",
                str(workspace),
                "--target",
                "Example",
                "--market",
                "SG",
                "--purpose",
                "Track public product questions",
            )
            self.assertTrue((workspace / "evidence.sqlite3").is_file())
            self.assertTrue((workspace / "discovery" / "platform_census.csv").is_file())

            first = write_bundle(
                root,
                "first",
                "2026-08-01T00:00:00+00:00",
                [
                    content("A", "one", "10", "t1"),
                    content("B", "two", "20", "t1"),
                ],
            )
            second = write_bundle(
                root,
                "second",
                "2026-08-02T00:00:00+00:00",
                [
                    content("A", "one", "10", "t2"),
                    content("B", "two", "25", "t2"),
                    content("C", "three", "30", "t2"),
                ],
            )
            third = write_bundle(
                root,
                "third",
                "2026-08-03T00:00:00+00:00",
                [content("A", "one", "10", "t3")],
            )

            self.run_cli("refresh", "--workspace", str(workspace), "--bundle", str(first))
            second_result = self.run_cli(
                "refresh", "--workspace", str(workspace), "--bundle", str(second)
            )
            self.assertIn("1 new", second_result.stdout)
            self.assertIn("2 updated", second_result.stdout)
            third_result = self.run_cli(
                "refresh", "--workspace", str(workspace), "--bundle", str(third)
            )
            self.assertIn("2 not observed", third_result.stdout)

            with (workspace / "current" / "content.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                current = {row["record_id"]: row for row in csv.DictReader(handle)}
            self.assertEqual(set(current), {"A", "B", "C"})
            self.assertEqual(current["B"]["views"], "25")

            history = self.run_cli(
                "history", "--workspace", str(workspace), "--type", "content", "--id", "B"
            )
            history_json = json.loads(history.stdout)
            self.assertEqual(len(history_json["observations"]), 2)
            self.assertEqual(
                [item["row"]["views"] for item in history_json["observations"]], ["20", "25"]
            )

            duplicate = self.run_cli(
                "refresh", "--workspace", str(workspace), "--bundle", str(third)
            )
            self.assertIn("already imported", duplicate.stdout)
            with sqlite3.connect(workspace / "evidence.sqlite3") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 3)

            validation = self.run_cli("validate", "--workspace", str(workspace))
            self.assertTrue(json.loads(validation.stdout)["valid"])

    def test_scope_is_stable_across_different_date_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            self.run_cli("discover", "--workspace", str(workspace), "--target", "Example")
            first = write_bundle(
                root,
                "first",
                "2026-08-01T00:00:00+00:00",
                [content("A", "one", "10", "t1"), content("B", "two", "20", "t1")],
            )
            second = write_bundle(
                root,
                "second",
                "2026-09-01T00:00:00+00:00",
                [content("A", "one", "11", "t2")],
            )
            first_manifest = json.loads((first / "run_manifest.json").read_text(encoding="utf-8"))
            second_manifest = json.loads((second / "run_manifest.json").read_text(encoding="utf-8"))
            first_manifest["since"] = "2026-07-01"
            second_manifest["since"] = "2026-08-01"
            (first / "run_manifest.json").write_text(json.dumps(first_manifest), encoding="utf-8")
            (second / "run_manifest.json").write_text(json.dumps(second_manifest), encoding="utf-8")
            self.run_cli("refresh", "--workspace", str(workspace), "--bundle", str(first))
            result = self.run_cli(
                "refresh", "--workspace", str(workspace), "--bundle", str(second)
            )
            self.assertIn("1 not observed", result.stdout)
            with sqlite3.connect(workspace / "evidence.sqlite3") as connection:
                scopes = connection.execute("SELECT DISTINCT scope_key FROM runs").fetchall()
            self.assertEqual(len(scopes), 1)

    def test_validation_detects_archived_source_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            self.run_cli("discover", "--workspace", str(workspace), "--target", "Example")
            bundle = write_bundle(
                root,
                "bundle",
                "2026-08-01T00:00:00+00:00",
                [content("A", "one", "10", "t1")],
            )
            self.run_cli("refresh", "--workspace", str(workspace), "--bundle", str(bundle))
            archive = next((workspace / "captures").iterdir())
            with (archive / "content.csv").open("a", encoding="utf-8") as handle:
                handle.write("tampered\n")
            result = self.run_cli("validate", "--workspace", str(workspace), check=False)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
