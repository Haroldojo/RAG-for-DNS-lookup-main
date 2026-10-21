"""Public-target validation shared by read-only HTTP diagnostics."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeTargetError(ValueError):
    pass


def normalize_public_url(url_or_domain: str) -> str:
    value = url_or_domain.strip()
    if not value:
        raise UnsafeTargetError("target is required")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeTargetError("only http and https schemes are supported")
    if not parsed.hostname:
        raise UnsafeTargetError("target must include a hostname")
    validate_public_hostname(parsed.hostname)
    return parsed.geturl()


def validate_public_hostname(hostname: str) -> None:
    normalized = hostname.rstrip(".").lower()
    if normalized in {"localhost", "metadata.google.internal"} or normalized.endswith(".localhost"):
        raise UnsafeTargetError("localhost and metadata targets are blocked")
    try:
        addresses = [
            item[4][0] for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        ]
    except socket.gaierror as error:
        raise UnsafeTargetError("dns_resolution_failed") from error
    if not addresses:
        raise UnsafeTargetError("dns_resolution_failed")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeTargetError(
                "private, loopback, link-local, or reserved targets are blocked"
            )
