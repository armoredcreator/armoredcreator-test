"""Pipeline efêmero do ArmoredStudioEdit.

O ArmoredStudioEdit chama somente:
- ArmoredStudio.analysis.blackbar
- ArmoredStudio.analysis.veo_detector
- ArmoredStudio.analysis.gemini_detector
- ArmoredStudio.processing.rvc

Nenhum orquestrador do ArmoredStudio existente é importado.
O workspace é temporário e seu ciclo de vida pertence ao chamador.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ArmoredStudio.analysis import blackbar, gemini_detector, veo_detector
from ArmoredStudio.processing import rvc


class ArmoredStudioEditPipeline:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def selected_operations(self) -> list[str]:
        ops = ["blackbar", "veo", "gemini"]
        if bool(self.config.get("rvc", {}).get("enabled")):
            ops.append("rvc")
        return ops

    def _ffmpeg(self) -> str:
        return str(self.config.get("ffmpeg", {}).get("binary", "ffmpeg"))

    def _run_ffmpeg(self, args: list[str]) -> None:
        result = subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "FFmpeg falhou.")

    def _crop_if_needed(self, source: Path, target: Path, bars: dict[str, Any]) -> Path:
        if not bars.get("detectado"):
            shutil.copy2(source, target)
            return target
        filt = blackbar.gerar_filtro_crop(bars)
        if not filt:
            shutil.copy2(source, target)
            return target
        self._run_ffmpeg([
            "-i", str(source), "-vf", filt, "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-c:a", "copy", str(target),
        ])
        return target

    def _apply_rvc(self, source: Path, target: Path, voice: str, work: Path) -> Path:
        original_audio = work / "audio_original.wav"
        rvc_audio = work / "audio_rvc.wav"
        self._run_ffmpeg([
            "-i", str(source), "-vn", "-ac", "1", "-ar", "44100",
            "-c:a", "pcm_s16le", str(original_audio),
        ])
        rvc.converter_voz(original_audio, rvc_audio, voz=voice)
        self._run_ffmpeg([
            "-i", str(source), "-i", str(rvc_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(target),
        ])
        return target

    def run(self, video: str | Path, workspace: str | Path) -> dict[str, Any]:
        source = Path(video).resolve()
        work = Path(workspace).resolve()
        work.mkdir(parents=True, exist_ok=True)

        if not source.is_file():
            raise FileNotFoundError(f"Vídeo não encontrado: {source}")

        current = source
        report: dict[str, Any] = {
            "input": str(source),
            "operations": self.selected_operations(),
            "detections": {},
        }

        report["detections"]["blackbar"] = blackbar.analisar_bordas_video(current)
        next_file = work / "01_blackbar.mp4"
        self._crop_if_needed(current, next_file, report["detections"]["blackbar"])
        current = next_file

        report["detections"]["veo"] = veo_detector.detect_fast(str(current))
        report["detections"]["gemini"] = gemini_detector.detect_fast(str(current))

        rvc_config = self.config.get("rvc", {})
        if bool(rvc_config.get("enabled")):
            voice = str(rvc_config.get("voice") or rvc.DEFAULT_VOICE)
            next_file = work / "02_rvc.mp4"
            self._apply_rvc(current, next_file, voice, work)
            current = next_file
            report["rvc_voice"] = voice

        final = work / "final.mp4"
        if current != final:
            shutil.copy2(current, final)

        report["output"] = str(final)
        report["status"] = "DONE"
        return report
