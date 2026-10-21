import json
import logging
from datetime import UTC, datetime
from typing import Any


def configure_logging() -> None:
    """Configure a small, predictable baseline suitable for local development."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit JSON-shaped application events without logging sensitive request content."""
    forbidden = {
        "api_key",
        "password",
        "authorization",
        "cookie",
        "prompt",
        "user_query",
        "query",
        "content",
    }
    fields = {
        key: "[redacted]" if key.lower() in forbidden else value for key, value in fields.items()
    }
    logger.info(
        json.dumps(
            {"timestamp": datetime.now(UTC).isoformat(), "level": "INFO", "event": event, **fields},
            sort_keys=True,
            default=str,
        )
    )
