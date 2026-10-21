from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Agentic DNS & Hosting Support Copilot"
    app_env: str = "development"
    debug: bool = False

    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
    groq_timeout_seconds: float = 20.0
    groq_temperature: float = 0.1
    groq_max_tokens: int = 500
    database_url: str | None = None
    redis_url: str | None = None
    redis_socket_timeout_seconds: float = 0.2
    dns_cache_ttl_seconds: int = 60
    rag_cache_ttl_seconds: int = 300
    rag_cache_version: str = "v1"
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60
    ssl_cache_ttl_seconds: int = 300
    http_cache_ttl_seconds: int = 30
    ssl_timeout_seconds: float = 5.0
    http_timeout_seconds: float = 5.0
    http_max_redirects: int = 3
    human_review_confidence_threshold: float = 0.7
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    rag_collection_name: str = "support_docs"
    rag_embedding_model: str = "BAAI/bge-small-en-v1.5"
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120

    def validate_production(self) -> None:
        """Fail early with configuration names only; never echo secret values."""
        if self.app_env.lower() != "production":
            return
        missing = [
            name
            for name, value in {
                "DATABASE_URL": self.database_url,
                "REDIS_URL": self.redis_url,
                "QDRANT_URL": self.qdrant_url,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"production configuration missing: {', '.join(missing)}")
        if self.debug:
            raise RuntimeError("production configuration invalid: DEBUG must be false")


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings object per process."""
    return Settings()
