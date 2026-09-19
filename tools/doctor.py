from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "__pycache__", "storage"}


def py_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if not any(part in EXCLUDED for part in p.parts)]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def main() -> int:
    files = py_files()
    modules = {p.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__") for p in files}
    roots = {m.split(".")[0] for m in modules}
    referenced = set()
    for path in files:
        referenced |= {name for name in imports(path) if name in roots}
    print(f"python_files={len(files)}")
    print(f"project_roots={sorted(roots)}")
    print(f"referenced_roots={sorted(referenced)}")
    print("CONFIG=config/settings.json")
    print("SECRETS=environment-only")
    print("ORPHAN_SCAN=reference-based; entrypoints must be reviewed before deleting source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
