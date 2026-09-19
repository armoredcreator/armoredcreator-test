from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from armored_core.models import Item
from armored_core.services import StudioResult
from armored_core.storage import Storage


class ArmoredStudio:
    """Single-job video processing boundary.

    FFmpeg is the real processor when available. A byte-for-byte copy is
    allowed only when explicitly enabled for deterministic offline tests.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.storage = Storage(self.root)

    def process(self, item: Item) -> StudioResult:
        source = Path(item.working_path or item.original_path)
        if not source.exists():
            raise FileNotFoundError(source)

        workspace = self.storage.workspace(item.item_id)
        working = self.storage.working(item.item_id)
        output = self.storage.result(
            item.item_id,
            affiliate_url=item.affiliate_url,
            affiliate_name=item.affiliate_name,
        )

        if source != working:
            shutil.copy2(source, working)
        source = working

        ffmpeg = os.getenv("ARMORED_FFMPEG", "ffmpeg")
        if os.getenv("ARMORED_STUDIO_FORCE_COPY", "0") == "1" and os.getenv("ARMORED_STUDIO_ALLOW_COPY", "0") == "1":
            shutil.copy2(source, output)
        elif shutil.which(ffmpeg):
            cmd = [
                ffmpeg, "-y", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-movflags", "+faststart", str(output),
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(f"Studio/FFmpeg falhou: {completed.stderr[-1200:]}")
        elif os.getenv("ARMORED_STUDIO_ALLOW_COPY", "0") == "1":
            shutil.copy2(source, output)
        else:
            raise RuntimeError("FFmpeg não encontrado e fallback de cópia não está habilitado")

        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("Studio produziu uma saída inválida")
        return StudioResult(working, output)


def build(root: Path, **_kwargs):
    return ArmoredStudio(root)
