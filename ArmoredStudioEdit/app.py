"""Entry point do ArmoredStudioEdit."""
from __future__ import annotations
import json
from pathlib import Path
from .pipeline.orchestrator import ArmoredStudioEditPipeline

def load_config() -> dict:
    path = Path(__file__).parent / "config" / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> None:
    config = load_config()
    pipeline = ArmoredStudioEditPipeline(config)
    print("ArmoredStudioEdit")
    print("Operações selecionadas:", ", ".join(pipeline.selected_operations()) or "nenhuma")

if __name__ == "__main__":
    main()
