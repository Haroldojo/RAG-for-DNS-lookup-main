"""Read-only deployment smoke checks; run after migrations and document ingestion."""

from __future__ import annotations

import os
import sys

import httpx


def main() -> None:
    base = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1")
    client = httpx.Client(timeout=10)
    checks = {"health": client.get(f"{base}/health"), "ready": client.get(f"{base}/ready")}
    for name, response in checks.items():
        print(f"{name}: {response.status_code}")
        if response.status_code != 200:
            sys.exit(1)
    search = client.post(
        f"{base}/api/v1/search", json={"query": "What is an A record?", "top_k": 1}
    )
    diagnosis = client.post(f"{base}/api/v1/diagnose", json={"query": "What is an A record?"})
    print(f"search: {search.status_code}; diagnose: {diagnosis.status_code}")
    if search.status_code != 200 or diagnosis.status_code != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
