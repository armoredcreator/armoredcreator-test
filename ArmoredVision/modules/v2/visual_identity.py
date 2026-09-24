from __future__ import annotations

import io
import os
from typing import Iterable

import requests


class CLIPVisualScorer:
    """Lazy CLIP image similarity scorer for V2 experiments.

    CLIP is an evidence source only; it never decides identity by itself.
    """

    def __init__(self, *, model_name: str | None = None, timeout: int = 12):
        self.model_name = model_name or os.getenv(
            "ARMORED_VISION_V2_CLIP_MODEL",
            "openai/clip-vit-base-patch32",
        )
        self.timeout = int(timeout)
        self._processor = None
        self._model = None
        self._torch = None
        self._device = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
        except ImportError as exc:
            raise RuntimeError(
                "CLIP V2 requer 'torch', 'transformers' e 'Pillow'. "
                "Instale as dependências antes do experimento."
            ) from exc

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._processor = CLIPProcessor.from_pretrained(
            self.model_name,
            token=False,
            use_fast=False,
        )
        self._model = CLIPModel.from_pretrained(
            self.model_name,
            token=False,
        ).to(self._device)
        self._model.eval()

    def _download(self, url: str):
        from PIL import Image

        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        return image

    def score_batch(self, reference_url: str, candidate_urls: Iterable[str]) -> list[float | None]:
        self._load()
        urls = [str(url or "").strip() for url in candidate_urls]
        result: list[float | None] = [None] * len(urls)
        valid = [(index, url) for index, url in enumerate(urls) if url]
        if not reference_url or not valid:
            return result

        try:
            reference = self._download(reference_url)
        except (requests.RequestException, OSError, ValueError):
            return result

        images = []
        indices = []
        for index, url in valid:
            try:
                images.append(self._download(url))
                indices.append(index)
            except (requests.RequestException, OSError, ValueError):
                continue

        if not images:
            return result

        with self._torch.no_grad():
            ref_inputs = self._processor(images=reference, return_tensors="pt")
            ref_inputs = {key: value.to(self._device) for key, value in ref_inputs.items()}
            ref_features = self._model.get_image_features(**ref_inputs)
            ref_features = ref_features / ref_features.norm(dim=-1, keepdim=True)

            inputs = self._processor(images=images, return_tensors="pt")
            inputs = {key: value.to(self._device) for key, value in inputs.items()}
            features = self._model.get_image_features(**inputs)
            features = features / features.norm(dim=-1, keepdim=True)
            scores = (features @ ref_features.T).squeeze(1).detach().cpu().tolist()

        for index, score in zip(indices, scores):
            result[index] = float(score)
        return result
