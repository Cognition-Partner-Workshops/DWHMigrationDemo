from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

DIMENSION = 384
MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashEmbedder:
    dim = DIMENSION

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in text.casefold().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                vector[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class FastEmbedEmbedder:
    dim = DIMENSION

    def __init__(self, cache_dir: Path | None = None) -> None:
        from fastembed import TextEmbedding

        self.cache_dir = cache_dir or Path(
            os.environ.get("MIGRAAI_MODEL_DIR", Path(__file__).resolve().parents[3] / "models")
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        model_exists = any(self.cache_dir.rglob("model_optimized.onnx")) or any(
            self.cache_dir.rglob("model.onnx")
        )
        self.model = TextEmbedding(
            model_name=MODEL_NAME,
            cache_dir=str(self.cache_dir),
            local_files_only=model_exists,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, vector)) for vector in self.model.embed(list(texts))]


def get_embedder() -> Embedder:
    if os.environ.get("MIGRAAI_EMBEDDER", "").casefold() == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder()
