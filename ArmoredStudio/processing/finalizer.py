from pathlib import Path
import json
import subprocess
import sys

try:
    from .tool_paths import resolve_executable
except ImportError:
    from tool_paths import resolve_executable

VIDEO_PRESET = "medium"
VIDEO_CRF = "18"
VIDEO_PROFILE = "high"
VIDEO_LEVEL = "4.0"
VIDEO_PIX_FMT = "yuv420p"
AUDIO_BITRATE = "192k"

VOICE_VOLUME = 1.0
MUSIC_VOLUME = 0.8
INTRO_MUSIC_VOLUME = 2.5

INTRO_DURATION = 2.0
INTRO_WIDTH = 1080
INTRO_HEIGHT = 1920
BLUR = "boxblur=6:2"
FADE_IN = "fade=t=in:st=0:d=0.6:color=white"
FADE_OUT = "fade=t=out:st=1.7:d=0.6:color=white"
BRIGHTNESS = "0.00"
CONTRAST = "1.10"
SATURATION = "1.12"
GAMMA = "1.00"
UNSHARP = "5:5:0.5:5:5:0.0"

# ArmoredStudio root. External tool paths configured relatively must resolve
# from the project, not from whichever directory launched finalizer.py.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _windows_hidden_kwargs():
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def validar_arquivo(arquivo, descricao):
    arquivo = Path(arquivo)
    if not arquivo.exists():
        raise FileNotFoundError(f"{descricao} não encontrado:\n{arquivo}")
    if arquivo.stat().st_size <= 0:
        raise RuntimeError(f"{descricao} está vazio:\n{arquivo}")


def probe_video(video):
    ffprobe = resolve_executable(
        "ffprobe",
        env_var="ARMORED_FFPROBE",
        project_root=PROJECT_ROOT,
    )
    comando = [
        str(ffprobe), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", str(video)
    ]
    resultado = subprocess.run(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_windows_hidden_kwargs(),
    )
    if resultado.returncode != 0:
        raise RuntimeError(
            "Não foi possível obter a resolução/FPS do vídeo:\n"
            + resultado.stderr.strip()
        )
    dados = json.loads(resultado.stdout)
    streams = dados.get("streams") or []
    if not streams:
        raise RuntimeError("Vídeo sem stream de vídeo válido.")
    stream = streams[0]
    return int(stream["width"]), int(stream["height"]), stream.get("r_frame_rate", "30/1")


def criar_filtro(position, largura, altura, fps):
    # Entrada 0: vídeo principal.
    visual = (
        f"[0:v]"
        f"eq=brightness={BRIGHTNESS}:contrast={CONTRAST}:"
        f"saturation={SATURATION}:gamma={GAMMA},"
        f"unsharp={UNSHARP},"
        f"setpts=PTS-STARTPTS"
        f"[main_v]"
    )

    # Entrada 3: banner. A intro é construída diretamente no mesmo FFmpeg.
    intro = (
        f"[0:v]trim=duration={INTRO_DURATION},setpts=PTS-STARTPTS,"
        f"scale={INTRO_WIDTH}:2133,"
        f"crop={INTRO_WIDTH}:{INTRO_HEIGHT}:x=(iw-{INTRO_WIDTH})/2:y=(ih-{INTRO_HEIGHT})/2,"
        f"{BLUR},{FADE_IN},{FADE_OUT}"
        f"[intro_bg];"
        f"[3:v]scale={INTRO_WIDTH}:{INTRO_HEIGHT}[banner];"
        f"[intro_bg][banner]overlay=0:0,"
        f"fps={fps},setpts=PTS-STARTPTS"
        f"[intro_v0];"
        f"[intro_v0]trim=duration={INTRO_DURATION},setpts=PTS-STARTPTS,scale={largura}:{altura}:force_original_aspect_ratio=increase,"
        f"crop={largura}:{altura}:(iw-{largura})/2:(ih-{altura})/2,"
        f"setsar=1,setpts=PTS-STARTPTS"
        f"[intro_v]"
    )

    # Música principal + RVC: preserva exatamente os níveis usados pela V2 atual.
    audio_main = (
        f"[1:a]volume={VOICE_VOLUME}[voice];"
        f"[2:a]loudnorm=I=-12:LRA=7:TP=-1,volume={MUSIC_VOLUME}[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=2"
        f"[main_a]"
    )

    # Música da intro: preserva o tratamento atual da intro.
    audio_intro = (
        f"[2:a]afade=t=in:st=0:d=0.4,"
        f"afade=t=out:st=1.5:d=0.5,"
        f"loudnorm=I=-5:LRA=7:TP=-1,"
        f"volume={INTRO_MUSIC_VOLUME},"
        f"atrim=duration={INTRO_DURATION},asetpts=PTS-STARTPTS"
        f"[intro_a]"
    )

    concat_order = (
        "[intro_v][intro_a][main_v][main_a]"
        if position == "inicio"
        else "[main_v][main_a][intro_v][intro_a]"
    )

    concat = f"{concat_order}concat=n=2:v=1:a=1[vout][aout]"

    return ";".join([visual, intro, audio_main, audio_intro, concat])


def finalizar(video, voz, musica, banner, saida, position="final", intro=True):
    video = Path(video)
    voz = Path(voz)
    musica = Path(musica)
    banner = Path(banner)
    saida = Path(saida)

    validar_arquivo(video, "Vídeo")
    validar_arquivo(voz, "Áudio RVC")
    validar_arquivo(musica, "Música")
    if intro:
        validar_arquivo(banner, "Banner")
        if position not in {"inicio", "final"}:
            raise RuntimeError(f"Posição de intro inválida: {position}")

    largura, altura, fps = probe_video(video)
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()

    filtro = criar_filtro(position, largura, altura, fps) if intro else (
        f"[0:v]eq=brightness={BRIGHTNESS}:contrast={CONTRAST}:"
        f"saturation={SATURATION}:gamma={GAMMA},unsharp={UNSHARP},"
        f"setpts=PTS-STARTPTS[main_v];"
        f"[1:a]volume={VOICE_VOLUME}[voice];"
        f"[2:a]loudnorm=I=-12:LRA=7:TP=-1,volume={MUSIC_VOLUME}[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )

    ffmpeg = resolve_executable(
        "ffmpeg",
        env_var="ARMORED_FFMPEG",
        project_root=PROJECT_ROOT,
    )
    comando = [
        str(ffmpeg), "-y",
        "-i", str(video),
        "-i", str(voz),
        "-stream_loop", "-1",
        "-i", str(musica),
    ]
    if intro:
        comando += ["-loop", "1", "-i", str(banner)]

    comando += [
        "-filter_complex", filtro,
        "-map", "[vout]" if intro else "[main_v]",
        "-map", "[aout]" if intro else "[main_a]",
        "-c:v", "libx264",
        "-preset", VIDEO_PRESET,
        "-crf", VIDEO_CRF,
        "-profile:v", VIDEO_PROFILE,
        "-level", VIDEO_LEVEL,
        "-pix_fmt", VIDEO_PIX_FMT,
        "-g", "30",
        "-keyint_min", "30",
        "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(saida),
    ]

    print()
    print("================================")
    print(" FINALIZAÇÃO V2 — FFMPEG ÚNICO")
    print("================================")
    print(f"Intro: {position.upper() if intro else 'DESATIVADA'}")
    print(f"Voz: {VOICE_VOLUME * 100:.0f}%")
    print(f"Música vídeo: {MUSIC_VOLUME * 100:.0f}%")
    if intro:
        print(f"Música intro: {INTRO_MUSIC_VOLUME * 100:.0f}%")
    print(f"Ordem: {'INTRO -> VIDEO' if position == 'inicio' else 'VIDEO -> INTRO' if intro else 'VIDEO'}")
    print("================================")

    resultado = subprocess.run(comando, **_windows_hidden_kwargs())
    if resultado.returncode != 0:
        raise RuntimeError("Erro na finalização FFmpeg da V2.")

    validar_arquivo(saida, "Resultado final")
    print("Resultado final criado:")
    print(saida)
    return saida


def main():
    if len(sys.argv) not in {5, 7, 8}:
        print("Uso:")
        print("python finalizer.py video.mp4 voz.wav musica.wav banner.png saida.mp4 [inicio|final] [intro_on|intro_off]")
        sys.exit(1)

    video, voz, musica, banner, saida = sys.argv[1:6]
    position = sys.argv[6] if len(sys.argv) >= 7 else "final"
    intro = True
    if len(sys.argv) == 8:
        intro = sys.argv[7].lower() != "intro_off"

    finalizar(video, voz, musica, banner, saida, position, intro)


if __name__ == "__main__":
    main()
