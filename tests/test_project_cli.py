from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectCliTest(unittest.TestCase):
    def test_doctor_and_export_help(self) -> None:
        for command in ("doctor", "export", "install-skill"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "competitor-census"), command, "--help"],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertIn("usage:", result.stdout.lower())

    def test_installer_links_repository_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["CODEX_HOME"] = str(Path(tmp) / "codex-home")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/install_skill.py"), "--target", "codex"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            linked = Path(env["CODEX_HOME"]) / "skills" / "competitor-census"
            self.assertTrue(linked.is_symlink())
            self.assertEqual(linked.resolve(), ROOT.resolve())
            self.assertIn("linked", result.stdout)


if __name__ == "__main__":
    unittest.main()
