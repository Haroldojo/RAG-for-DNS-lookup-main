"""Small safe observability primitives; all telemetry is best-effort."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from app.core.logging import log_event
from app.observability.taxonomy import classify_error

LOGGER = logging.getLogger(__name__)
request_context: ContextVar[dict[str, str] | None] = ContextVar("request_context", default=None)


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.latencies: dict[str, list[float]] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def observe(self, name: str, value: float) -> None:
        self.latencies.setdefault(name, []).append(value)

    def snapshot(self) -> dict[str, object]:
        def percentile(values: list[float], fraction: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 2)

        return {
            "counters": dict(self.counters),
            "latency_ms": {
                name: {
                    "count": len(values),
                    "average": round(sum(values) / len(values), 2),
                    "p50": percentile(values, 0.5),
                    "p95": percentile(values, 0.95),
                }
                for name, values in self.latencies.items()
                if values
            },
        }


metrics = Metrics()


def begin_request(request_id: str | None = None, **ids: str | None) -> dict[str, str]:
    context = {"request_id": request_id or str(uuid4()), "trace_id": str(uuid4())}
    context.update({key: value for key, value in ids.items() if value})
    request_context.set(context)
    return context


def context() -> dict[str, str]:
    return dict(request_context.get() or {})


def event(component: str, operation: str, status: str, **fields: object) -> None:
    """Emit safe JSON event with automatically propagated correlation IDs."""
    merged = {**context(), **fields}
    if merged.get("error_type"):
        merged["error_category"] = str(classify_error(merged["error_type"]))
    log_event(
        LOGGER,
        "operational_event",
        component=component,
        operation=operation,
        status=status,
        **merged,
    )


def timed(component: str, operation: str):
    """Context manager recording duration even when a dependency fails."""

    class Timer:
        def __enter__(self):
            self.started = perf_counter()
            event(component, operation, "started")
            return self

        def __exit__(self, exc_type, exc, _tb):
            latency = round((perf_counter() - self.started) * 1000, 2)
            state = "failed" if exc_type else "ok"
            event(
                component,
                operation,
                state,
                latency_ms=latency,
                error_type=exc_type.__name__ if exc_type else None,
            )
            metrics.observe(f"{component}.{operation}", latency)
            return False

    return Timer()
