"""ArmoredStudio V2 - RVC voice processor."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent
VOICE_DIR = BASE_DIR / "voice"
MODELS_DIR = VOICE_DIR / "models"
RVC_ENV_DIR = VOICE_DIR / "rvc_env"

DEFAULT_VOICE = "melody"
DEFAULT_OUTPUT = VOICE_DIR / "output" / "audio_rvc.wav"


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
    if not indices:
        raise FileNotFoundError(f"Nenhum índice .index encontrado em:\n{pasta}")
    return modelos[0], indices[0]


def converter_interno(entrada, saida, modelo, index):
    from rvc_python.infer import RVCInference
    import scipy.io.wavfile as wavfile

    print("\nCarregando modelo RVC...")
    rvc = RVCInference(
        model_path=str(modelo),
        index_path=str(index),
        version="v2",
        device="cpu:0",
    )
    print("Modelo carregado.\n\nConvertendo voz...")
    resultado = rvc.infer_file(str(entrada), str(saida))
    if isinstance(resultado, tuple):
        sample_rate, audio = resultado
        wavfile.write(str(saida), sample_rate, audio)
    validar_arquivo(saida, "Áudio RVC")
    print("\nConversão RVC concluída:")
    print(saida)
    return Path(saida)


def executar_no_rvc(entrada, saida, voz):
    modelo, index = localizar_modelo(voz)
    rvc_python = resolver_rvc_python()

    print("\n=== RVC VOICE ===")
    print(f"Voz: {voz}")
    print(f"Modelo: {modelo}")
    print(f"Index: {index}")

    current_python = Path(sys.executable).resolve()
    if current_python == rvc_python.resolve():
        return converter_interno(entrada, saida, modelo, index)

    comando = [
        str(rvc_python),
        str(Path(__file__).resolve()),
        str(entrada),
        str(saida),
        voz,
    ]
    print("\nExecutando ambiente RVC:")
    print(" ".join(map(str, comando)))

    resultado = subprocess.run(comando, cwd=str(BASE_DIR))
    if resultado.returncode != 0:
        raise RuntimeError("Falha na conversão RVC.")
    validar_arquivo(saida, "Áudio RVC")
    return Path(saida)


def converter_voz(entrada, saida=None, voz=DEFAULT_VOICE):
    entrada = Path(entrada)
    saida = Path(saida) if saida is not None else DEFAULT_OUTPUT
    validar_arquivo(entrada, "Áudio de entrada")
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    return executar_no_rvc(entrada, saida, voz)


def main():
    if len(sys.argv) < 3:
        print("Uso: python rvc_voice.py entrada.wav saida.wav [voz]")
        sys.exit(1)

    entrada = Path(sys.argv[1])
    saida = Path(sys.argv[2])
    voz = sys.argv[3] if len(sys.argv) >= 4 else DEFAULT_VOICE

    if Path(sys.executable).resolve() == resolver_rvc_python().resolve():
        modelo, index = localizar_modelo(voz)
        converter_interno(entrada, saida, modelo, index)
        return

    converter_voz(entrada, saida, voz)


if __name__ == "__main__":
    main()
