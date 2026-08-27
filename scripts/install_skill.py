#!/usr/bin/env python3
"""Install this repository as a Codex and/or Claude Code Skill."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = {
    "codex": Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills" / "public-web-census",
    "claude": Path.home() / ".claude" / "skills" / "public-web-census",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        action="append",
        choices=[*sorted(DESTINATIONS), "all"],
        help="Agent skill to install; repeatable, or 'all' (default: codex)",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "link", "copy"),
        default="auto",
        help="auto links when possible and copies when symlinks are unavailable",
    )
    return parser.parse_args()


def same_location(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def main() -> int:
    args = parse_args()
    targets = args.target or ["codex"]
    if "all" in targets:
        targets = list(DESTINATIONS)
    for target in targets:
        destination = DESTINATIONS[target].expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if same_location(destination, ROOT):
                print(f"[OK] {target}: {destination} already points to this repository")
                continue
            raise SystemExit(
                f"Refusing to replace existing path: {destination}\n"
                "Move or remove it yourself after reviewing its contents, then rerun this command."
            )
        if args.mode != "copy":
            try:
                destination.symlink_to(ROOT, target_is_directory=True)
                print(f"[OK] {target}: linked {destination} -> {ROOT}")
                continue
            except OSError:
                if args.mode == "link":
                    raise
        shutil.copytree(
            ROOT,
            destination,
            ignore=shutil.ignore_patterns(".git", "runs", "__pycache__", ".DS_Store"),
        )
        print(f"[OK] {target}: copied Skill to {destination}")
    print("Restart or open a new Agent session, then invoke $public-web-census.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
