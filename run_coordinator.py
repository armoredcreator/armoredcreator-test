from __future__ import annotations

import logging
import os
from pathlib import Path

from armored_core.coordinator import Coordinator


def main() -> int:
    root = Path(os.getenv("ARMORED_ROOT") or Path(__file__).resolve().parent).resolve()
    os.environ.setdefault("ARMORED_ROOT", str(root))
    # O processo real do Coordinator deve usar o ArmoredSync/Telegram real.
    # LocalSource continua disponível apenas para testes offline via Coordinator.build().
    os.environ.setdefault("ARMORED_REAL_TELEGRAM", "1")

    logging.basicConfig(
        level=os.getenv("ARMORED_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    coordinator = Coordinator.build(root=root)
    try:
        logging.info("ArmoredCreator Coordinator iniciado")
        logging.info("Root: %s", root)
        logging.info("Modo SQLite: %s", coordinator.db.sync_mode())
        logging.info("Sync real Telegram: %s", os.getenv("ARMORED_REAL_TELEGRAM"))
        logging.info("Fonte Sync: %s", os.getenv("ARMORED_SYNC_SOURCE") or "-1003788989075")
        coordinator.run_forever(
            poll_seconds=float(os.getenv("ARMORED_POLL_SECONDS", "2")),
        )
        return 0
    except KeyboardInterrupt:
        logging.info("Shutdown solicitado pelo operador")
        return 0
    except SystemExit:
        return 0
    except Exception:
        logging.exception("Coordinator encerrou com erro")
        return 1
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
