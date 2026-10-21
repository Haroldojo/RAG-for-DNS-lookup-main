from dataclasses import dataclass
from typing import Protocol


class LLMProviderError(Exception):
    """Base class for normalized provider failures."""


class MissingAPIKeyError(LLMProviderError):
    pass


class LLMTimeoutError(LLMProviderError):
    pass


class RateLimitError(LLMProviderError):
    pass


class ProviderUnavailableError(LLMProviderError):
    pass


class MalformedResponseError(LLMProviderError):
    pass


class EmptyResponseError(LLMProviderError):
    pass


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model_name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMProvider(Protocol):
    model_name: str

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult: ...
