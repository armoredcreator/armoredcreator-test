"""Orquestrador isolado; não implementa nem altera ArmoredStudio."""
from __future__ import annotations
from pathlib import Path
from typing import Any

class ArmoredStudioEditPipeline:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def selected_operations(self) -> list[str]:
        p = self.config.get("processing", {})
        ops = []
        if p.get("remove_black_bars"): ops.append("remove_black_bars")
        if p.get("veo"): ops.append("veo")
        if p.get("gemini"): ops.append("gemini")
        if p.get("rvc") or self.config.get("rvc", {}).get("enabled"): ops.append("rvc")
        return ops

    def run(self, video: str | Path) -> dict[str, Any]:
        return {"input": str(video), "operations": self.selected_operations(), "status": "PLANNED"}
