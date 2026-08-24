from __future__ import annotations

import argparse
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import local_analysis


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class FakeClient:
    def __init__(self, *_args: object) -> None:
        self.model = "fake-local"

    def chat_json(self, _system: str, user: str, schema: dict[str, object]) -> object:
        properties = schema.get("properties", {})
        if "categories" in properties:
            return {
                "categories": [
                    {
                        "id": "how_to",
                        "label": "How-to content",
                        "definition": "Content that explains setup or use.",
                        "inclusion_criteria": ["The primary purpose is instruction."],
                        "exclusion_criteria": ["Instruction is only incidental."],
                    }
                ]
            }
        ids = []
        marker = '"id": "'
        remainder = user.split("Rows:\n", 1)[-1]
        while marker in remainder:
            remainder = remainder.split(marker, 1)[1]
            value, _, remainder = remainder.partition('"')
            ids.append(value)
        return {
            "records": [
                {
                    "record_id": record_id,
                    "text_translation": "Local translation",
                    "content_type": "how_to",
                    "classification_confidence": "high",
                    "classification_notes": "The visible text is instructional.",
                }
                for record_id in ids
            ]
        }


class LocalAnalysisTest(unittest.TestCase):
    def test_normalizes_numeric_confidence_and_taxonomy_ids(self) -> None:
        taxonomy = local_analysis.normalize_taxonomy(
            {
                "categories": [
                    {
                        "id": "How To",
                        "label": "How to",
                        "definition": "Instruction.",
                        "inclusion_criteria": ["Instruction."],
                        "exclusion_criteria": ["Not instruction."],
                    }
                ]
            },
            "content",
        )
        rows = local_analysis.normalize_result_rows(
            [
                {
                    "record_id": "R-1",
                    "text_translation": "Instruction",
                    "content_type": "how_to",
                    "classification_confidence": ".95",
                    "classification_notes": "Visible instruction.",
                }
            ],
            "content",
            taxonomy,
        )
        self.assertEqual(taxonomy[0]["id"], "how_to")
        self.assertEqual(rows[0]["classification_confidence"], "high")

    def test_runner_fills_and_validates_content_packet_with_local_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            for name in ("content.csv", "comments.csv", "platform_census.csv"):
                shutil.copy(ROOT / "demo/input" / name, bundle / name)
            (bundle / "run_manifest.json").write_text(
                json.dumps({"target": {"company": "Northstar"}}), encoding="utf-8"
            )
            original = local_analysis.OllamaClient
            local_analysis.OllamaClient = FakeClient  # type: ignore[assignment]
            try:
                result = local_analysis.run(
                    argparse.Namespace(
                        bundle=bundle,
                        mode="content",
                        model="fake-local",
                        host="http://fake",
                        target_language="English",
                        batch_size=100,
                        taxonomy_batch_size=100,
                        context=32768,
                        timeout=10,
                        force=False,
                        no_report=True,
                    )
                )
            finally:
                local_analysis.OllamaClient = original
            self.assertEqual(result, 0)
            self.assertTrue((bundle / "analysis" / "taxonomy.json").exists())
            self.assertTrue((bundle / "analyzed_content.csv").exists())
            self.assertEqual(len(read_csv(bundle / "analyzed_content.csv")), len(read_csv(bundle / "content.csv")))


if __name__ == "__main__":
    unittest.main()
