from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect removable Python cache artifacts safely.")
    parser.add_argument("--delete", action="store_true", help="Delete only __pycache__ and .pyc artifacts.")
    args = parser.parse_args()
    candidates = list(ROOT.rglob("__pycache__")) + list(ROOT.rglob("*.pyc"))
    for path in candidates:
        print(f"CANDIDATE {path.relative_to(ROOT)}")
        if args.delete:
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    if child.is_file(): child.unlink()
                    elif child.is_dir(): child.rmdir()
                path.rmdir()
            elif path.is_file():
                path.unlink()
    print(f"count={len(candidates)} deleted={args.delete}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
