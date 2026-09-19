from __future__ import annotations

import os
from pathlib import Path

from .production import build_legacy_coordinator


def main() -> None:
    root = Path(os.getenv("ARMORED_ROOT", Path.cwd())).resolve()
    coordinator = build_legacy_coordinator(root)
    try:
        coordinator.recover_pending()
        coordinator.process_next()
    finally:
        coordinator.close()


if __name__ == "__main__":
    main()
