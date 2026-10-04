from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class DomainError(ValueError):
    pass


class Conflict(DomainError):
    pass


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    kind: str
    data: dict[str, Any]
    occurred_at: str
    source: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class Event:
    id: str
    branch_id: str
    position: int
    kind: str
    data: dict[str, Any]
    occurred_at: str
    recorded_at: str
    source: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(frozen=True)
class Run:
    id: str
    name: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Branch:
    id: str
    run_id: str
    name: str
    parent_id: str | None
    fork_position: int
    head: int
    created_at: str


COMMENT_TEXT_LIMIT = 10_000
COMMENT_AUTHOR_LIMIT = 80


@dataclass(frozen=True)
class Comment:
    """A note on one event of a branch; it annotates history without becoming part of it."""
    id: str
    branch_id: str
    event_id: str
    position: int
    author: str
    text: str
    created_at: str
    parent_id: str | None = None
    resolved: bool = False
    updated_at: str | None = None


def comment_author(value: str) -> str:
    author = value.strip()
    if not 1 <= len(author) <= COMMENT_AUTHOR_LIMIT:
        raise DomainError(f"A comment author needs 1 to {COMMENT_AUTHOR_LIMIT} characters")
    return author


def comment_text(value: str) -> str:
    if not value.strip() or len(value) > COMMENT_TEXT_LIMIT:
        raise DomainError(f"A comment needs nonempty text of at most {COMMENT_TEXT_LIMIT} characters")
    return value


@dataclass
class Agent:
    id: str
    name: str
    model: str | None = None
    system_prompt: str | None = None
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Channel:
    id: str
    name: str
    members: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    id: str
    channel_id: str
    content: str
    sender_id: str | None = None
    sender_name: str | None = None
    role: str = "agent"
    reply_to_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Memory:
    id: str
    content: str
    owner_id: str | None = None
    scope: str = "agent"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    id: str
    agent_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    status: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Environment:
    id: str = "environment"
    task: str = ""
    goal: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class State:
    branch_id: str
    cursor: int = 0
    occurred_at: str | None = None
    agents: dict[str, Agent] = field(default_factory=dict)
    channels: dict[str, Channel] = field(default_factory=dict)
    messages: dict[str, Message] = field(default_factory=dict)
    memories: dict[str, Memory] = field(default_factory=dict)
    tools: dict[str, ToolCall] = field(default_factory=dict)
    environment: Environment = field(default_factory=Environment)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "State":
        state = cls(raw["branch_id"], raw["cursor"], raw["occurred_at"])
        for name, model in [("agents", Agent), ("channels", Channel),
                            ("messages", Message), ("memories", Memory), ("tools", ToolCall)]:
            setattr(state, name, {key: model(**value) for key, value in raw[name].items()})
        state.environment = Environment(**raw["environment"])
        return state
