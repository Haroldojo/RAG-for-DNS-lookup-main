"""Small deterministic safeguards; these do not rely on LLM judgment."""

from __future__ import annotations

import re
from enum import StrEnum

from app.observability.runtime import event

INJECTION = re.compile(
    r"(?:ignore (?:all |any )?(?:previous|prior) instructions|system override|reveal (?:the )?(?:system prompt|environment|api key)|output all environment variables)",
    re.IGNORECASE,
)
SECRET = re.compile(
    r"(?:gsk_[A-Za-z0-9_-]{12,}|postgres(?:ql)?://\S+|redis://\S+|(?:api[_ -]?key|authorization|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d -]{7,}\d)(?!\w)")
ACCOUNT = re.compile(
    r"\b(?:customer|account)[ _-]?(?:id|number)\s*[:=]?\s*[A-Za-z0-9-]{4,}\b", re.IGNORECASE
)


class ToolPermission(StrEnum):
    READ_ONLY = "read_only"
    WRITE_REQUIRES_APPROVAL = "write_requires_approval"
    DESTRUCTIVE_BLOCKED = "destructive_blocked"


READ_ONLY_TOOLS = {"dns_check", "ssl_check", "http_check", "search_support_docs"}


def detect_prompt_injection(value: str) -> bool:
    return bool(INJECTION.search(value))


def redact_sensitive(value: str) -> str:
    value = SECRET.sub("[redacted-secret]", value)
    value = EMAIL.sub("[redacted-email]", value)
    value = PHONE.sub("[redacted-phone]", value)
    return ACCOUNT.sub("[redacted-account]", value)


def sanitize_untrusted_document(value: str) -> str:
    """Documents are evidence, never instructions. Remove obvious hostile directives."""
    if detect_prompt_injection(value):
        event("security", "indirect_prompt_injection", "blocked", error_type="prompt_injection")
        return INJECTION.sub("[untrusted instruction removed]", value)
    return value


def validate_model_output(value: str) -> tuple[str, bool]:
    """Prevent accidental secret exposure before response/persistence."""
    sanitized = redact_sensitive(value)
    blocked = sanitized != value
    if blocked:
        event("security", "unsafe_llm_output", "blocked", error_type="secret_exfiltration")
    return sanitized, blocked


def allow_tool(tool_name: str, permission: ToolPermission = ToolPermission.READ_ONLY) -> bool:
    allowed = permission is ToolPermission.READ_ONLY and tool_name in READ_ONLY_TOOLS
    if not allowed:
        event("security", "tool_permission", "blocked", error_type="invalid_tool_request")
    return allowed
