"""Observability — lightweight Prometheus-compatible metrics.

Provides in-process metrics without requiring ``prometheus_client``.
Exports metrics in the standard OpenMetrics text format that Prometheus
can scrape directly.

Metric types
------------
* :class:`Counter` — monotonically increasing integer
* :class:`Gauge` — integer or float that can go up or down
* :class:`Histogram` — bucket-based distribution of observed values

Usage::

    from bauer.observability import MetricsRegistry

    reg = MetricsRegistry(namespace="bauer")
    tasks_total = reg.counter("tasks_completed_total", "Total tasks completed")
    budget_cost = reg.gauge("budget_cost_usd", "Current budget cost in USD")
    latency = reg.histogram("task_duration_seconds", "Task duration",
                             buckets=[0.1, 0.5, 1.0, 5.0, 10.0])

    tasks_total.inc()
    budget_cost.set(0.42)
    latency.observe(1.23)

    print(reg.render())  # OpenMetrics text
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _Metric:
    """Abstract base for all metric types."""

    def __init__(
        self,
        name: str,
        help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.labels = labels or {}

    def _label_str(self) -> str:
        if not self.labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(self.labels.items())]
        return "{" + ",".join(parts) + "}"

    def render(self) -> str:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


class Counter(_Metric):
    """Monotonically increasing counter."""

    def __init__(self, name: str, help_text: str = "",
                 labels: dict[str, str] | None = None) -> None:
        super().__init__(name, help_text, labels)
        self._value: int = 0
        self._created: float = time.time()

    def inc(self, amount: int = 1) -> None:
        """Increment by *amount* (must be ≥ 1)."""
        if amount < 0:
            raise ValueError("Counter increment must be non-negative")
        self._value += max(0, amount)

    @property
    def value(self) -> int:
        return self._value

    def reset(self) -> None:
        self._value = 0

    def render(self) -> str:
        ls = self._label_str()
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
            f"{self.name}_total{ls} {self._value}",
            f"{self.name}_created{ls} {self._created:.3f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gauge
# ---------------------------------------------------------------------------


class Gauge(_Metric):
    """Arbitrary numeric value that can increase or decrease."""

    def __init__(self, name: str, help_text: str = "",
                 labels: dict[str, str] | None = None) -> None:
        super().__init__(name, help_text, labels)
        self._value: float = 0.0

    def set(self, value: float) -> None:
        self._value = float(value)

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    @property
    def value(self) -> float:
        return self._value

    def render(self) -> str:
        ls = self._label_str()
        v = int(self._value) if self._value == int(self._value) else self._value
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} gauge",
            f"{self.name}{ls} {v}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------

_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Histogram(_Metric):
    """Distribution of observed values across fixed buckets."""

    def __init__(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] | list[float] = _DEFAULT_BUCKETS,
        labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name, help_text, labels)
        self._buckets = sorted(float(b) for b in buckets)
        self._counts = [0] * len(self._buckets)
        self._inf_count: int = 0
        self._sum: float = 0.0
        self._count: int = 0
        self._created: float = time.time()

    def observe(self, value: float) -> None:
        """Record one observation."""
        self._sum += value
        self._count += 1
        placed = False
        for i, b in enumerate(self._buckets):
            if value <= b:
                self._counts[i] += 1
                if not placed:
                    placed = True
        # +Inf bucket always gets the observation
        self._inf_count += 1

    @property
    def count(self) -> int:
        return self._count

    @property
    def sum(self) -> float:
        return self._sum

    def render(self) -> str:
        ls = self._label_str()
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        # Cumulative bucket counts
        cumulative = 0
        for i, b in enumerate(self._buckets):
            cumulative += self._counts[i]
            b_str = str(int(b)) if b == int(b) else str(b)
            if ls:
                inner = ls[1:-1] + f',le="{b_str}"'
                label_part = "{" + inner + "}"
            else:
                label_part = '{' + f'le="{b_str}"' + '}'
            lines.append(f"{self.name}_bucket{label_part} {cumulative}")
        # +Inf bucket
        if ls:
            inf_label = "{" + ls[1:-1] + ',le="+Inf"}'
        else:
            inf_label = '{le="+Inf"}'
        lines.append(f"{self.name}_bucket{inf_label} {self._inf_count}")
        lines.append(f"{self.name}_sum{ls} {self._sum}")
        lines.append(f"{self.name}_count{ls} {self._count}")
        lines.append(f"{self.name}_created{ls} {self._created:.3f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MetricsRegistry
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Central registry that manages and renders all metrics.

    Parameters
    ----------
    namespace:
        Optional prefix added to all metric names (e.g. ``"bauer"``).
    """

    def __init__(self, namespace: str = "") -> None:
        self._namespace = namespace
        self._metrics: dict[str, _Metric] = {}

    def _full_name(self, name: str) -> str:
        return f"{self._namespace}_{name}" if self._namespace else name

    def counter(
        self, name: str, help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> Counter:
        full = self._full_name(name)
        if full not in self._metrics:
            self._metrics[full] = Counter(full, help_text, labels)
        return self._metrics[full]  # type: ignore[return-value]

    def gauge(
        self, name: str, help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> Gauge:
        full = self._full_name(name)
        if full not in self._metrics:
            self._metrics[full] = Gauge(full, help_text, labels)
        return self._metrics[full]  # type: ignore[return-value]

    def histogram(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] | list[float] = _DEFAULT_BUCKETS,
        labels: dict[str, str] | None = None,
    ) -> Histogram:
        full = self._full_name(name)
        if full not in self._metrics:
            self._metrics[full] = Histogram(full, help_text, buckets, labels)
        return self._metrics[full]  # type: ignore[return-value]

    def get(self, name: str) -> _Metric | None:
        return self._metrics.get(self._full_name(name))

    def names(self) -> list[str]:
        return list(self._metrics.keys())

    def render(self) -> str:
        """Return OpenMetrics text for all registered metrics."""
        parts = [m.render() for m in self._metrics.values()]
        return "\n".join(parts) + "\n# EOF\n" if parts else "# EOF\n"

    def snapshot(self) -> dict[str, Any]:
        """Return a dict of metric_name → current value (for JSON export)."""
        out: dict[str, Any] = {}
        for name, m in self._metrics.items():
            if isinstance(m, Counter):
                out[name] = m.value
            elif isinstance(m, Gauge):
                out[name] = m.value
            elif isinstance(m, Histogram):
                out[name] = {"count": m.count, "sum": m.sum}
        return out


# ---------------------------------------------------------------------------
# Bauer daemon metrics factory
# ---------------------------------------------------------------------------


def make_daemon_metrics(registry: MetricsRegistry | None = None) -> dict[str, Any]:
    """Create the standard daemon metrics and return them by name.

    If *registry* is None a new one is created.  Returns a dict of
    ``{name: metric_object}`` for convenient access.
    """
    reg = registry or MetricsRegistry(namespace="bauer")

    metrics = {
        "tasks_completed": reg.counter("tasks_completed_total",
                                        "Total tasks completed by the daemon"),
        "tasks_failed": reg.counter("tasks_failed_total",
                                     "Total tasks that failed"),
        "llm_calls": reg.counter("llm_calls_total", "Total LLM API calls"),
        "tool_calls": reg.counter("tool_calls_total", "Total tool executions"),
        "escalations": reg.counter("escalations_total", "Total escalations fired"),
        "budget_cost_usd": reg.gauge("budget_cost_usd",
                                      "Current accumulated budget cost in USD"),
        "budget_pct": reg.gauge("budget_cost_pct",
                                 "Budget cost as percentage of max"),
        "workers_active": reg.gauge("workers_active", "Number of active workers"),
        "goals_active": reg.gauge("goals_active", "Number of active goals"),
        "task_duration_seconds": reg.histogram(
            "task_duration_seconds",
            "Task execution duration in seconds",
            buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
        ),
        "registry": reg,
    }
    return metrics
