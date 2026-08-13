#!/usr/bin/env python3
"""Install this repository as a Codex and/or Claude Code Skill."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = {
    "codex": Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills" / "competitor-census",
    "claude": Path.home() / ".claude" / "skills" / "competitor-census",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        action="append",
        choices=sorted(DESTINATIONS),
        help="Agent skill directory to link; repeatable (default: codex)",
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
        destination.symlink_to(ROOT, target_is_directory=True)
        print(f"[OK] {target}: linked {destination} -> {ROOT}")
    print("Restart or open a new Agent session, then invoke $competitor-census.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
