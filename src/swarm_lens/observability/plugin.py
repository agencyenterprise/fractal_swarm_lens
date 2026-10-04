from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from swarm_lens.core.findings import Report
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
    """An analyzer that runs a fresh sequential method over the adapter's observations of start..end.

    `params` is an optional pydantic model whose dump is the method factory's configuration.
    """

    def __init__(self, method_id: str, version: str,
                 factory: Callable[[dict], ObservabilityMethod], adapter: HistoryAdapter, params: type | None = None):
        self.id, self.version, self.title = method_id, version, method_id.upper()
        self.factory, self.adapter, self.Params = factory, adapter, params

    def analyze(self, view, start, end, params):
        method = self.factory({} if params is None else params.model_dump())
        if method.id != self.id or method.version != self.version:
            raise ValueError("Method identity must match the versioned plugin")
        results = []
        for turn in self.adapter.observations(view.events(start, end)):
            results.append(dict(method.update(turn)))
            if method.finished:
                break
        yield Report({
            "method": {"id": method.id, "version": method.version, **method.describe()},
            "adapter": {"id": self.adapter.id, "version": self.adapter.version, **self.adapter.describe()},
            "turns": results,
        })
