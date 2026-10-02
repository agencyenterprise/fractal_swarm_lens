from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelEvent:
    """Already encoded, causally ordered source/observed-target pair.

    Target must be actual downstream behavior, not a copy of the source payload.
    Feature schema, exposure, turn boundaries and encoding belong to the application.
    """

    source: str
    target: str
    channel: str
    source_vector: tuple[float, ...]
    target_vector: tuple[float, ...]


@dataclass(frozen=True)
class Turn:
    index: int
    events: tuple[ChannelEvent, ...]
