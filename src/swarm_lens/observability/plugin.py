from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from swarm_lens.core.models import Event


class ObservabilityMethod(Protocol):
    """A fresh, sequential method instance for one selected history prefix."""

    id: str
    version: str

    @property
    def finished(self) -> bool: ...

    def update(self, observation: Any) -> Mapping[str, Any]: ...
    def describe(self) -> Mapping[str, Any]: ...


class HistoryAdapter(Protocol):
    """Application-owned mapping; never infer source semantics in the framework."""

    id: str
    version: str

    def observations(self, history: list[Event]) -> Iterable[Any]: ...
    def describe(self) -> Mapping[str, Any]: ...


class ObservabilityPlugin:
    def __init__(self, method_id: str, version: str,
                 factory: Callable[[dict], ObservabilityMethod], adapter: HistoryAdapter):
        self.id, self.version = f"observability.{method_id}", version
        self.method_id, self.factory, self.adapter = method_id, factory, adapter

    def run(self, context, config):
        method = self.factory(config)
        if method.id != self.method_id or method.version != self.version:
            raise ValueError("Method identity must match the versioned plugin")
        results = []
        for turn in self.adapter.observations(context.history()):
            results.append(dict(method.update(turn)))
            if method.finished:
                break
        return {
            "method": {"id": method.id, "version": method.version, **method.describe()},
            "adapter": {"id": self.adapter.id, "version": self.adapter.version,
                        **self.adapter.describe()},
            "turns": results,
        }
