#!/usr/bin/env python3
"""Check the Public Web Census runtime and optional live connectors."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


OPENCLI_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/opencli/"
    "ildkmabpimmkaediidaifkhjpohdnifk"
)


def command_result(command: list[str], timeout: int = 45) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, output


def chrome_path() -> str | None:
    windows_roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        *(str(Path(root) / "Google/Chrome/Application/chrome.exe") for root in windows_roots if root),
    ]
    return next((str(path) for path in candidates if path and Path(path).exists()), None)


def ytdlp_command() -> list[str] | None:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    return [sys.executable, "-m", "yt_dlp"] if importlib.util.find_spec("yt_dlp") else None


def main() -> int:
    required_ok = True
    python_ok = sys.version_info >= (3, 10)
    print(f"[{'OK' if python_ok else 'FAIL'}] Python: {sys.version.split()[0]} (3.10+ required)")
    required_ok &= python_ok

    opencli = shutil.which("opencli")
    if opencli:
        version_ok, version = command_result([opencli, "--version"], timeout=15)
        print(f"[{'OK' if version_ok else 'WARN'}] OpenCLI: {version or opencli}")
        bridge_ok, bridge = command_result([opencli, "doctor"])
        summary = next((line for line in bridge.splitlines() if "Connectivity:" in line), "browser bridge check completed")
        print(f"[{'OK' if bridge_ok else 'WARN'}] Browser bridge: {summary.strip()}")
        if not bridge_ok:
            print("       Live TikTok/Facebook/LinkedIn collection needs Chrome plus the OpenCLI extension.")
            print(f"       Guided setup: ./public-web-census setup")
            print(f"       Extension: {OPENCLI_EXTENSION_URL}")
    else:
        print("[WARN] OpenCLI: not installed (needed for live TikTok/Facebook/LinkedIn collection)")
        print("       Guided setup: ./public-web-census setup")

    ytdlp = ytdlp_command()
    if ytdlp:
        ok, version = command_result([*ytdlp, "--version"], timeout=15)
        source = " (Python module)" if ytdlp[0] == sys.executable else ""
        print(f"[{'OK' if ok else 'WARN'}] yt-dlp: {version or 'available'}{source}")
    else:
        print("[WARN] yt-dlp: not installed (needed only for live YouTube collection)")
        print('       python3 -m pip install -U "yt-dlp[default]"')

    chrome = chrome_path()
    print(f"[{'OK' if chrome else 'WARN'}] Chrome/Chromium: {chrome or 'not found (needed for browser collection and optional PDF export)'}")
    print("[OK] Offline demo and evidence validators use the Python standard library only.")
    return 0 if required_ok else 1


if __name__ == "__main__":
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print(__doc__)
        print("\nUsage: ./public-web-census doctor")
        raise SystemExit(0)
    raise SystemExit(main())
