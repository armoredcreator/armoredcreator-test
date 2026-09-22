"""CLI do ArmoredStudioEdit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline.orchestrator import ArmoredStudioEditPipeline


CONFIG_PATH = Path(__file__).parent / "config" / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="ArmoredStudioEdit")
    parser.add_argument("video", nargs="?", help="Vídeo local para processar")
    parser.add_argument("--output", help="Arquivo final")
    args = parser.parse_args()

    config = load_config()
    pipeline = ArmoredStudioEditPipeline(config)
    print("ArmoredStudioEdit")
    print("Operações:", ", ".join(pipeline.selected_operations()) or "nenhuma")

    if not args.video:
        return 0

    report = pipeline.run(args.video, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
