#!/usr/bin/env python3
"""Guide live-connector setup for Competitor Census."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


OPENCLI_PACKAGE = "@jackwener/opencli"
OPENCLI_EXTENSION_URL = (
    "https://chromewebstore.google.com/detail/opencli/"
    "ildkmabpimmkaediidaifkhjpohdnifk"
)
OPENCLI_PROJECT_URL = "https://github.com/jackwener/OpenCLI"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Set up the optional browser bridge used for live TikTok and Facebook "
            "collection. Extension approval and platform login always remain manual."
        )
    )
    parser.add_argument(
        "--install-opencli",
        action="store_true",
        help="install OpenCLI with npm when it is missing",
    )
    parser.add_argument(
        "--with-youtube",
        action="store_true",
        help="also install yt-dlp for YouTube metadata collection when missing",
    )
    parser.add_argument(
        "--open-extension",
        action="store_true",
        help="open the official OpenCLI Chrome Web Store page when not connected",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="inspect setup without installing packages, opening a page, or restarting services",
    )
    return parser.parse_args()


def run_capture(command: list[str], timeout: int = 60) -> tuple[int, str]:
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
        return 1, str(exc)
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return completed.returncode, output


def run_visible(command: list[str]) -> bool:
    try:
        return subprocess.run(command, check=False).returncode == 0
    except OSError as exc:
        print(f"[FAIL] Could not run {' '.join(command)}: {exc}", file=sys.stderr)
        return False


def ask(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(prompt + suffix).strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer in {"y", "yes"}


def node_major(node: str) -> int | None:
    code, output = run_capture([node, "--version"], timeout=15)
    if code != 0:
        return None
    value = output.strip().lstrip("v").split(".", 1)[0]
    return int(value) if value.isdigit() else None


def chrome_path() -> str | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((str(path) for path in candidates if path and Path(path).exists()), None)


def open_extension_page(chrome: str | None) -> bool:
    try:
        if chrome:
            subprocess.Popen(
                [chrome, OPENCLI_EXTENSION_URL],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        return bool(webbrowser.open(OPENCLI_EXTENSION_URL, new=2))
    except OSError:
        return bool(webbrowser.open(OPENCLI_EXTENSION_URL, new=2))


def bridge_connected(opencli: str) -> tuple[bool, str]:
    code, output = run_capture([opencli, "doctor"])
    connected = (
        code == 0
        and "[OK] Extension:" in output
        and "[OK] Connectivity:" in output
        and "connected" in output.lower()
    )
    detail = next(
        (line.strip() for line in output.splitlines() if "Connectivity:" in line),
        "OpenCLI doctor did not report a connected browser bridge.",
    )
    return connected, detail


def install_opencli(npm: str) -> bool:
    print(f"\nInstalling {OPENCLI_PACKAGE} with npm …")
    return run_visible([npm, "install", "-g", OPENCLI_PACKAGE])


def install_ytdlp() -> bool:
    print("\nInstalling yt-dlp with the active Python …")
    return run_visible([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"])


def main() -> int:
    args = parse_args()
    interactive = sys.stdin.isatty() and not args.check_only
    print("Competitor Census live-connector setup\n")

    python_ok = sys.version_info >= (3, 10)
    print(f"[{'OK' if python_ok else 'FAIL'}] Python {sys.version.split()[0]} (3.10+ required)")
    if not python_ok:
        return 1

    chrome = chrome_path()
    print(f"[{'OK' if chrome else 'WARN'}] Chrome/Chromium: {chrome or 'not found'}")

    npm = shutil.which("npm")
    node = shutil.which("node")
    major = node_major(node) if node else None
    node_ok = bool(npm and major is not None and major >= 21)
    node_label = f"Node {major}" if major is not None else "Node not found"
    print(f"[{'OK' if node_ok else 'WARN'}] {node_label}; npm {'found' if npm else 'not found'} (Node 21+ required by OpenCLI)")

    opencli = shutil.which("opencli")
    wants_install = args.install_opencli
    if not opencli and not args.check_only and not wants_install and interactive and node_ok:
        wants_install = ask("OpenCLI is missing. Install it now with npm?")
    if not opencli and wants_install:
        if not node_ok or not npm:
            print("[FAIL] Install Node.js 21+ and npm first, then rerun this command.")
        elif install_opencli(npm):
            opencli = shutil.which("opencli")
            print(f"[{'OK' if opencli else 'WARN'}] OpenCLI installation command completed")
        else:
            print("[FAIL] OpenCLI installation failed. Review npm output above.")

    if not opencli:
        print("[ACTION] OpenCLI is required for live TikTok/Facebook collection.")
        print(f"         npm install -g {OPENCLI_PACKAGE}")
        print(f"         Project: {OPENCLI_PROJECT_URL}")
    else:
        code, version = run_capture([opencli, "--version"], timeout=15)
        print(f"[{'OK' if code == 0 else 'WARN'}] OpenCLI: {version or opencli}")

    extension_ok = False
    if opencli:
        extension_ok, detail = bridge_connected(opencli)
        print(f"[{'OK' if extension_ok else 'ACTION'}] Chrome extension/browser bridge: {detail}")

    wants_extension_page = args.open_extension
    if opencli and not extension_ok and not args.check_only and not wants_extension_page and interactive:
        wants_extension_page = ask("Open the official OpenCLI extension page in Chrome?")
    if opencli and not extension_ok and wants_extension_page:
        if open_extension_page(chrome):
            print(f"[OPENED] {OPENCLI_EXTENSION_URL}")
            print("         Click ‘Add to Chrome’, keep Chrome open, and approve the extension.")
        else:
            print(f"[ACTION] Open this URL in Chrome: {OPENCLI_EXTENSION_URL}")

    if opencli and not extension_ok and interactive and not args.check_only:
        try:
            input("\nAfter approving the extension, press Enter to verify the connection … ")
        except EOFError:
            pass
        run_visible([opencli, "daemon", "restart"])
        extension_ok, detail = bridge_connected(opencli)
        print(f"[{'OK' if extension_ok else 'ACTION'}] Chrome extension/browser bridge: {detail}")

    ytdlp = shutil.which("yt-dlp")
    if args.with_youtube and not ytdlp and not args.check_only:
        if install_ytdlp():
            ytdlp = shutil.which("yt-dlp")
    print(f"[{'OK' if ytdlp else 'OPTIONAL'}] yt-dlp: {ytdlp or 'not installed; needed only for YouTube'}")

    print("\nHuman steps that the setup assistant cannot and should not automate:")
    print("  1. Approve the official OpenCLI extension in Chrome.")
    print("  2. Sign into TikTok/Facebook directly in the Chrome profile you will use.")
    print("  3. Complete any platform human-verification challenge yourself.")
    print("\nThe assistant never asks for a platform password or writes browser credentials into this repository.")

    if opencli and extension_ok:
        print("\n[READY] Browser bridge connected. Run './competitor-census doctor' at any time to recheck it.")
        return 0
    print("\n[NOT READY] Finish the ACTION items above, then rerun './competitor-census setup'.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
