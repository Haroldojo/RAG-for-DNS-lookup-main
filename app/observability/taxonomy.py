"""Stable, payload-free error categories for logs, metrics and traces."""

from enum import StrEnum


class ErrorCategory(StrEnum):
    VALIDATION = "validation_error"
    RETRIEVAL = "retrieval_error"
    DNS = "dns_error"
    SSL = "ssl_error"
    HTTP = "http_error"
    REDIS = "redis_error"
    DATABASE = "database_error"
    LLM = "llm_error"
    ESCALATION = "escalation_error"
    UNKNOWN = "unknown_error"


def classify_error(error: object) -> ErrorCategory:
    value = str(error).lower()
    for marker, category in (
        ("retrieval", ErrorCategory.RETRIEVAL),
        ("dns", ErrorCategory.DNS),
        ("ssl", ErrorCategory.SSL),
        ("http", ErrorCategory.HTTP),
        ("redis", ErrorCategory.REDIS),
        ("database", ErrorCategory.DATABASE),
        ("llm", ErrorCategory.LLM),
        ("validation", ErrorCategory.VALIDATION),
        ("escalation", ErrorCategory.ESCALATION),
    ):
        if marker in value:
            return category
    return ErrorCategory.UNKNOWN
