#!/usr/bin/env python3
"""Export an evidence-linked HTML report to a vector PDF."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def find_chrome(explicit: str | None) -> str:
    candidates = [
        explicit,
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for value in candidates:
        if value and Path(value).expanduser().is_file():
            return str(Path(value).expanduser())
    raise FileNotFoundError("Chrome or Chromium was not found; pass --chrome /path/to/browser")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True, help="Input HTML report")
    parser.add_argument("--pdf", type=Path, help="Output PDF (default: input name with .pdf)")
    parser.add_argument("--chrome", help="Chrome/Chromium executable path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.html.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"HTML report not found: {source}")
    destination = (args.pdf or source.with_suffix(".pdf")).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        chrome = find_chrome(args.chrome)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={destination}",
            source.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if completed.returncode or not destination.is_file() or destination.stat().st_size == 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(detail or "Chrome did not create the PDF")
    print(f"PDF: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
