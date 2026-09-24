"""ArmoredStudio - isolated RVC voice processor."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent
STUDIO_ROOT = BASE_DIR.parent
_configured_rvc_root = os.getenv("ARMORED_RVC_ROOT", "").strip()
RVC_ROOT = (
    Path(_configured_rvc_root).expanduser()
    if _configured_rvc_root
    else STUDIO_ROOT / "runtime" / "rvc"
).resolve()
MODELS_DIR = RVC_ROOT / "models"
RVC_ENV_DIR = RVC_ROOT / "env"

DEFAULT_VOICE = "melody"
DEFAULT_OUTPUT = RVC_ROOT / "output" / "audio_rvc.wav"


def resolver_rvc_python() -> Path:
    """Resolve the local RVC Python for the current operating system."""
    candidates = (
        RVC_ENV_DIR / "Scripts" / "python.exe",
        RVC_ENV_DIR / "bin" / "python",
        RVC_ENV_DIR / "bin" / "python3",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    expected = "\n".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Python do ambiente RVC não encontrado. Caminhos esperados:\n" + expected
    )


def validar_arquivo(arquivo, descricao):
    arquivo = Path(arquivo)
    if not arquivo.exists():
        raise FileNotFoundError(f"{descricao} não encontrado:\n{arquivo}")
    if arquivo.stat().st_size <= 0:
        raise RuntimeError(f"{descricao} está vazio:\n{arquivo}")


def localizar_modelo(voz):
    voz = str(voz).strip()
    if not voz:
        raise ValueError("Nome da voz não pode ser vazio.")
    pasta = MODELS_DIR / voz
    if not pasta.exists():
        raise FileNotFoundError(f"Voz não encontrada:\n{pasta}")

    modelos = sorted(pasta.glob("*.pth"))
    indices = sorted(pasta.glob("*.index"))

    if not modelos:
        raise FileNotFoundError(f"Nenhum modelo .pth encontrado em:\n{pasta}")

    # O índice é opcional: RVCInference aceita index_path="".
    index = indices[0] if indices else None
    return modelos[0], index


def converter_interno(entrada, saida, modelo, index, item_id=None):
    from rvc_python.infer import RVCInference
    import scipy.io.wavfile as wavfile

    prefix = f"[RVC][ITEM {item_id}] " if item_id else "[RVC] "
    print(f"\n{prefix}Iniciando conversão de voz")
    rvc = RVCInference(
        model_path=str(modelo),
        index_path=str(index) if index else "",
        version="v2",
        device="cpu:0",
    )
    print(f"{prefix}Modelo carregado; convertendo voz")

    try:
        # rvc-python's infer_file() writes its result with scipy internally.
        # When vc_single() fails, the library returns a traceback string
        # instead of an audio array; infer_file() then raises the misleading
        # "str has no attribute dtype". Call the underlying conversion
        # contract directly so failures remain deterministic and actionable.
        model_info = rvc.models[rvc.current_model]
        file_index = model_info.get("index", "")
        wav_opt = rvc.vc.vc_single(
            sid=0,
            input_audio_path=str(entrada),
            f0_up_key=rvc.f0up_key,
            f0_method=rvc.f0method,
            file_index=file_index,
            index_rate=rvc.index_rate,
            filter_radius=rvc.filter_radius,
            resample_sr=rvc.resample_sr,
            rms_mix_rate=rvc.rms_mix_rate,
            protect=rvc.protect,
            f0_file="",
            file_index2="",
        )
        if (
            isinstance(wav_opt, tuple)
            and len(wav_opt) == 2
            and isinstance(wav_opt[0], str)
            and isinstance(wav_opt[1], tuple)
        ):
            details = wav_opt[0].strip()
            Path(saida).unlink(missing_ok=True)
            raise RuntimeError(
                "RVC inference falhou no backend"
                + (f": {details}" if details else "")
            )

        if not hasattr(wav_opt, "dtype"):
            Path(saida).unlink(missing_ok=True)
            raise RuntimeError("RVC retornou áudio inválido")

        wavfile.write(str(saida), int(rvc.vc.tgt_sr), wav_opt)
    except Exception:
        Path(saida).unlink(missing_ok=True)
        raise

    try:
        validar_arquivo(saida, "Áudio RVC")
    except Exception:
        Path(saida).unlink(missing_ok=True)
        raise
    print(f"{prefix}CONCLUÍDO output={saida}")
    return Path(saida)


def executar_no_rvc(entrada, saida, voz, item_id=None):
    modelo, index = localizar_modelo(voz)
    rvc_python = resolver_rvc_python()

    prefix = f"[RVC][ITEM {item_id}]" if item_id else "[RVC]"
    print(f"\n{prefix} VOICE | voz={voz}")
    print(f"{prefix} modelo={modelo.name} index={index.name if index else '-'}")

    current_python = Path(sys.executable).resolve()
    if current_python == rvc_python.resolve():
        return converter_interno(entrada, saida, modelo, index, item_id)

    comando = [
        str(rvc_python),
        str(Path(__file__).resolve()),
        str(entrada),
        str(saida),
        voz,
        str(item_id or ""),
    ]
    print(f"{prefix} Executando ambiente RVC")

    resultado = subprocess.run(comando, cwd=str(BASE_DIR))
    if resultado.returncode != 0:
        raise RuntimeError("Falha na conversão RVC.")
    validar_arquivo(saida, "Áudio RVC")
    return Path(saida)


def converter_voz(entrada, saida=None, voz=DEFAULT_VOICE, item_id=None):
    entrada = Path(entrada)
    saida = Path(saida) if saida is not None else DEFAULT_OUTPUT
    validar_arquivo(entrada, "Áudio de entrada")
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    return executar_no_rvc(entrada, saida, voz, item_id)


def main():
    if len(sys.argv) < 3:
        print("Uso: python rvc.py entrada.wav saida.wav [voz]")
        sys.exit(1)

    entrada = Path(sys.argv[1])
    saida = Path(sys.argv[2])
    voz = sys.argv[3] if len(sys.argv) >= 4 else DEFAULT_VOICE
    item_id = sys.argv[4] if len(sys.argv) >= 5 and sys.argv[4] else None

    if Path(sys.executable).resolve() == resolver_rvc_python().resolve():
        modelo, index = localizar_modelo(voz)
        converter_interno(entrada, saida, modelo, index, item_id)
        return

    converter_voz(entrada, saida, voz, item_id)


if __name__ == "__main__":
    main()
