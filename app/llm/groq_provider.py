from typing import Any

from app.llm.provider import (
    EmptyResponseError,
    GenerationResult,
    LLMProviderError,
    LLMTimeoutError,
    MalformedResponseError,
    MissingAPIKeyError,
    ProviderUnavailableError,
    RateLimitError,
)


class GroqProvider:
    """Thin Groq adapter that normalizes provider-specific failures for the graph."""

    def __init__(
        self,
        api_key: str | None,
        model_name: str,
        timeout_seconds: float,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        if not self.api_key:
            raise MissingAPIKeyError("GROQ_API_KEY is not configured")
        try:
            from groq import Groq

            client = Groq(api_key=self.api_key, timeout=self.timeout_seconds)
            completion = client.chat.completions.create(
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as error:
            name = type(error).__name__.lower()
            if "rate" in name or "limit" in name or getattr(error, "status_code", None) == 429:
                raise RateLimitError("Groq rate limit reached") from error
            if "timeout" in name:
                raise LLMTimeoutError("Groq request timed out") from error
            if isinstance(error, LLMProviderError):
                raise
            raise ProviderUnavailableError("Groq provider is unavailable") from error
        try:
            choice = completion.choices[0]
            content = choice.message.content
        except (AttributeError, IndexError, TypeError) as error:
            raise MalformedResponseError("Groq response did not contain a valid message") from error
        if not isinstance(content, str):
            raise MalformedResponseError("Groq response content was not text")
        if not content.strip():
            raise EmptyResponseError("Groq returned an empty response")
        usage: Any = getattr(completion, "usage", None)
        return GenerationResult(
            text=content.strip(),
            model_name=self.model_name,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
