"""
ARMOREDSTUDIO V1 - VEO DETECTOR

Detector da marca VEO no canto inferior direito.

FAST:
- Analisa somente o frame 0.
- Não faz seeking.
- Usa a mesma comparação validada no detector anterior.
- ROI fixa proporcional à resolução.
- Template VEO embutido.
- Top-hat preprocessing.
- Threshold de decisão: 0.70.

FULL:
- Mantém a estratégia anterior de múltiplos frames.
- Disponível com --full.
"""

import argparse
import base64
import io
from pathlib import Path

import cv2
import numpy as np


# ============================================================
# TEMPLATE VEO
# ============================================================

TEMPLATE_B64 = """iVBORw0KGgoAAAANSUhEUgAAACkAAAAUCAAAAAA170tZAAABpklEQVQoFY3BQUhTcRzA8e9/P96fvzx5NBidFMSQRhAMi0gWQihBB4WgS2AJjcC6hR06lBB0qC4Kop6UHTt5CqIuQRR0cQWGENpgOBDG6tGD4fiPN9rCvTcLRp+PGvZrMDowbAS/tBdUa7RoI4d1uhlPTVC3uP1Ow5ofwTdLW4rDGsd4KU/NJOvVoNwIAkuH61Y4zk0ZT82dqJdKBbqYZhhyxNRpkZM6FDWXqBSKxIwOiGgsIJ5DLVTXq1+rxMRphHRoLC1GEzasurJTpifthA3EoiYKPm37K8+An1Mf+Ys4TUKxqOxuhbaZR2kYfz7GvwSxoLI7Pm3e5/M+7zaXuTb56jUwn/VzdFMDByF/vHy/yvdTfNj4srJ3ky1n6ezt9AERUXTMPhm6Mz01m7kPvwZH80Pw9OIkMUVkf/DNg+3FTNH2J27duJyD6RdpOgRFJO9mRng4kqMlu3gB1k9f4oiAInI1v3kXdtffzqfPsWWXxu+NfSKmiC1slIE12V4GFs6Ej4tEBMX/MX2K3kQct2m1TSYVvRmtHRGMTip6ESQhnminj8RvnmSFdGgXGFcAAAAASUVORK5CYII="""


# ============================================================
# CONFIGURAÇÃO
# ============================================================

ROI_X0 = 0.76
ROI_Y0 = 0.84

POSITIVE_THRESHOLD = 0.70
NEGATIVE_THRESHOLD = 0.45

AMBIGUOUS_EXTRA_FRAMES = (0.10, 0.50)

BASE_WIDTH = 720.0
BASE_TEMPLATE_WIDTH = 41
BASE_TEMPLATE_HEIGHT = 20

TOPHAT_KERNEL_720 = 15


# ============================================================
# TEMPLATE
# ============================================================

def load_template():
    data = base64.b64decode(TEMPLATE_B64)

    array = np.frombuffer(data, dtype=np.uint8)

    template = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)

    if template is None:
        raise RuntimeError("Não foi possível carregar o template VEO.")

    return template


# ============================================================
# INFORMAÇÕES DO VÍDEO
# ============================================================

def video_info(path):
    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(f"Não foi possível abrir o vídeo: {path}")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    cap.release()

    return frame_count, fps, width, height


# ============================================================
# TOP-HAT
# ============================================================

def build_tophat_kernel(width):
    scale = width / BASE_WIDTH

    size = int(round(TOPHAT_KERNEL_720 * scale))

    if size < 3:
        size = 3

    if size % 2 == 0:
        size += 1

    return cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (size, size),
    )


def apply_tophat(gray, kernel):
    return cv2.morphologyEx(
        gray,
        cv2.MORPH_TOPHAT,
        kernel,
    )


# ============================================================
# PREPARAÇÃO DO TEMPLATE
# ============================================================

def prepare_template(template_gray, width):
    scale = width / BASE_WIDTH

    target_width = max(
        1,
        int(round(BASE_TEMPLATE_WIDTH * scale)),
    )

    target_height = max(
        1,
        int(round(BASE_TEMPLATE_HEIGHT * scale)),
    )

    resized = cv2.resize(
        template_gray,
        (target_width, target_height),
        interpolation=cv2.INTER_AREA,
    )

    kernel = build_tophat_kernel(width)

    prepared = apply_tophat(
        resized,
        kernel,
    )

    return prepared


# ============================================================
# LEITURA DE FRAME
# ============================================================

def read_frame(cap, frame_number):
    cap.set(
        cv2.CAP_PROP_POS_FRAMES,
        int(frame_number),
    )

    ok, frame = cap.read()

    if not ok or frame is None:
        return None

    return frame


# ============================================================
# SCORE
# ============================================================

def score_frame(frame, template_gray):
    height, width = frame.shape[:2]

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    kernel = build_tophat_kernel(width)

    processed = apply_tophat(
        gray,
        kernel,
    )

    template = prepare_template(
        template_gray,
        width,
    )

    x0 = int(width * ROI_X0)
    y0 = int(height * ROI_Y0)

    roi = processed[
        y0:height,
        x0:width,
    ]

    template_height, template_width = template.shape[:2]

    if (
        roi.shape[1] < template_width
        or roi.shape[0] < template_height
    ):
        return -1.0, (0, 0, template_width, template_height)

    result = cv2.matchTemplate(
        roi,
        template,
        cv2.TM_CCOEFF_NORMED,
    )

    _, max_score, _, max_location = cv2.minMaxLoc(result)

    location = (
        x0 + max_location[0],
        y0 + max_location[1],
        template_width,
        template_height,
    )

    return float(max_score), location


# ============================================================
# FAST
# ============================================================

def detect_fast(path):
    template = load_template()

    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {path}"
        )

    ok, frame = cap.read()

    cap.release()

    if not ok or frame is None:
        raise RuntimeError(
            f"Não foi possível ler o frame 0: {path}"
        )

    score, location = score_frame(
        frame,
        template,
    )

    return {
        "found": score >= POSITIVE_THRESHOLD,
        "best_score": score,
        "scores": [(0, score)],
        "locations": [(0, location)],
        "frames_analyzed": 1,
    }


# ============================================================
# FULL
# ============================================================

def detect_full(path):
    template = load_template()

    frame_count, fps, width, height = video_info(path)

    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {path}"
        )

    scores = []
    locations = []

    def analyze(frame_number):
        frame = read_frame(
            cap,
            frame_number,
        )

        if frame is None:
            return None

        score, location = score_frame(
            frame,
            template,
        )

        scores.append(
            (frame_number, score)
        )

        locations.append(
            (frame_number, location)
        )

        return score

    # --------------------------------------------------------
    # Frame 0
    # --------------------------------------------------------

    first_score = analyze(0)

    if first_score is None:
        # Fallback exatamente no espírito do detector anterior.
        for frame_number in (1, 2, 3, 5):
            score = analyze(frame_number)

            if score is not None and score >= POSITIVE_THRESHOLD:
                break

    else:
        if first_score >= POSITIVE_THRESHOLD:
            pass

        elif first_score <= NEGATIVE_THRESHOLD:
            if frame_count > 1:
                analyze(
                    max(
                        0,
                        frame_count // 2,
                    )
                )

        else:
            for point in AMBIGUOUS_EXTRA_FRAMES:
                frame_number = int(
                    frame_count * point
                )

                if frame_number >= frame_count:
                    frame_number = frame_count - 1

                analyze(frame_number)

    cap.release()

    if not scores:
        raise RuntimeError(
            f"Nenhum frame pôde ser analisado: {path}"
        )

    best_frame, best_score = max(
        scores,
        key=lambda item: item[1],
    )

    return {
        "found": best_score >= POSITIVE_THRESHOLD,
        "best_score": best_score,
        "scores": scores,
        "locations": locations,
        "frames_analyzed": len(scores),
        "best_frame": best_frame,
        "frame_count": frame_count,
        "fps": fps,
        "width": width,
        "height": height,
    }


# ============================================================
# IMPRESSÃO
# ============================================================

def print_result(path, result, mode):
    print(f"\n[{Path(path).name}]")

    if mode == "fast":
        print("Analisando VEO no frame 0...")

    else:
        print("Analisando VEO no modo FULL...")

    print(
        f"Frames analisados: "
        f"{result['frames_analyzed']}"
    )

    score_text = ", ".join(
        f"frame {frame}={score:.3f}"
        for frame, score in result["scores"]
    )

    print(f"Scores: {score_text}")

    best_score = result["best_score"]

    print(
        f"Melhor score: {best_score:.3f}"
    )

    for frame, location in result["locations"]:
        if frame == result["scores"][
            max(
                range(len(result["scores"])),
                key=lambda i: result["scores"][i][1],
            )
        ][0]:
            print(
                f"Melhor localização: {location}"
            )
            break

    print(
        "RESULTADO FINAL: "
        + (
            "VEO ENCONTRADA."
            if result["found"]
            else "VEO NÃO ENCONTRADA."
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Detector VEO - ArmoredStudio V1"
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help="Executa o detector FULL.",
    )

    parser.add_argument(
        "videos",
        nargs="+",
        help="Vídeos para analisar.",
    )

    args = parser.parse_args()

    mode = "full" if args.full else "fast"

    resultados = []

    for video in args.videos:
        path = Path(video)

        if not path.exists():
            print(
                f"\n[{path.name}]"
            )
            print(
                f"ERRO: arquivo não encontrado: {path}"
            )
            continue

        try:
            if mode == "fast":
                result = detect_fast(path)
            else:
                result = detect_full(path)

            print_result(
                path,
                result,
                mode,
            )

            resultados.append(
                (
                    path.name,
                    result["found"],
                )
            )

        except Exception as exc:
            print(
                f"\n[{path.name}]"
            )
            print(
                f"ERRO: {exc}"
            )

    print("\n==============================")

    if mode == "fast":
        print("RESUMO VEO — FAST")
    else:
        print("RESUMO VEO — FULL")

    print("==============================")

    for name, found in resultados:
        print(
            f"{name}: "
            + (
                "VEO ENCONTRADA"
                if found
                else "SEM VEO"
            )
        )


if __name__ == "__main__":
    main()