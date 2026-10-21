import re
from collections import Counter
from hashlib import blake2b
from math import sqrt
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedProvider:
    """Small local ONNX embedding model; downloaded once then cached locally by FastEmbed."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self._dimensions = len(next(self._model.embed(["dimension probe"])).tolist())

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]


class HashingEmbeddingProvider:
    """Deterministic lightweight embedder used only for fast, offline tests."""

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            values = [0.0] * self._dimensions
            for token, count in Counter(re.findall(r"[a-z0-9]+", text.lower())).items():
                index = (
                    int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big")
                    % self._dimensions
                )
                values[index] += count
            norm = sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / norm for value in values])
        return vectors
