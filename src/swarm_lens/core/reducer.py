from copy import deepcopy
from dataclasses import asdict
from datetime import datetime

from .models import Agent, Channel, DomainError, Environment, Event, Memory, Message, State, ToolCall


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainError(message)


def _construct(model, data):
    try:
        value = model(**data)
    except TypeError as exc:
        raise DomainError(f"Invalid {model.__name__}: {exc}") from exc
    _require(isinstance(value.id, str) and bool(value.id.strip()), "An entity needs a nonempty id")
    return value


def apply(state: State, event: Event) -> None:
    """Apply one event to a privately owned state; no I/O or external dependencies."""
    _require(event.position == state.cursor + 1, "Event positions must be contiguous")
    try:
        timestamp = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
        _require(timestamp.tzinfo is not None, "Event timestamps must include a timezone")
    except (ValueError, AttributeError) as exc:
        raise DomainError("Invalid event timestamp") from exc
    data = deepcopy(event.data)
    kind = event.kind
    if kind == "agent.added":
        agent = _construct(Agent, data)
        _require(agent.id not in state.agents, "Agent already exists")
        _require(bool(agent.name.strip()), "Agent name is required")
        state.agents[agent.id] = agent
    elif kind == "agent.updated":
        agent_id = data.pop("id", None)
        _require(agent_id in state.agents, "Unknown agent")
        agent = _construct(Agent, {**asdict(state.agents[agent_id]), **data})
        _require(bool(agent.name.strip()), "Agent name is required")
        state.agents[agent_id] = agent
    elif kind == "agent.removed":
        _require(data.get("id") in state.agents, "Unknown agent")
        state.agents[data["id"]].active = False
        for channel in state.channels.values():
            channel.members = [member for member in channel.members if member != data["id"]]
    elif kind in ("channel.created", "channel.updated"):
        channel_id = data.get("id")
        if kind == "channel.created":
            _require(channel_id not in state.channels, "Channel already exists")
        else:
            _require(channel_id in state.channels, "Unknown channel")
            data = {**asdict(state.channels[channel_id]), **data}
        channel = _construct(Channel, data)
        _require(all(member in state.agents for member in channel.members), "Unknown channel member")
        _require(len(set(channel.members)) == len(channel.members), "Duplicate channel member")
        state.channels[channel.id] = channel
    elif kind == "message.created":
        message = _construct(Message, data)
        _require(message.id not in state.messages, "Message already exists")
        _require(message.channel_id in state.channels, "Unknown message channel")
        _require(message.role in ("agent", "human", "system", "tool"), "Unknown message role")
        if message.sender_id is not None:
            _require(message.sender_id in state.agents, "Unknown message sender")
            _require(state.agents[message.sender_id].active, "Removed agents cannot send new messages")
        _require(message.reply_to_id is None or message.reply_to_id in state.messages, "Unknown reply target")
        state.messages[message.id] = message
    elif kind == "memory.written":
        memory = _construct(Memory, data)
        _require(memory.owner_id is None or memory.owner_id in state.agents, "Unknown memory owner")
        _require(memory.scope in ("agent", "shared"), "Unknown memory scope")
        _require(memory.scope != "agent" or memory.owner_id is not None, "Agent memory needs an owner")
        state.memories[memory.id] = memory
    elif kind in ("tool.recorded", "tool.started", "tool.completed"):
        call_id = data.get("id")
        if kind == "tool.completed":
            _require(call_id in state.tools, "Unknown tool call")
            _require(state.tools[call_id].status == "running", "Tool call is not running")
            data = {**asdict(state.tools[call_id]), **data}
        else:
            _require(call_id not in state.tools, "Tool call already exists")
        call = _construct(ToolCall, data)
        _require(call.agent_id in state.agents, "Unknown tool caller")
        _require(call.status in ("unknown", "running", "completed", "failed"), "Unknown tool status")
        _require(kind != "tool.started" or call.status == "running", "Started tools must be running")
        _require(kind != "tool.completed" or call.status in ("completed", "failed"), "Completion needs a final status")
        state.tools[call.id] = call
    elif kind == "environment.updated":
        state.environment = _construct(Environment, {**asdict(state.environment), **data})
    elif kind == "observation.recorded":
        _require(isinstance(data.get("type"), str), "Observation needs a type")
        _require(data.get("agent_id") is None or data["agent_id"] in state.agents, "Unknown observation agent")
    else:
        raise DomainError(f"Unsupported event kind: {kind}")
    state.cursor = event.position
    state.occurred_at = event.occurred_at


def compare(left: State, right: State) -> dict:
    result = {}
    for collection in ("agents", "channels", "messages", "memories", "tools"):
        a = {key: asdict(value) for key, value in getattr(left, collection).items()}
        b = {key: asdict(value) for key, value in getattr(right, collection).items()}
        result[collection] = {
            "added": sorted(b.keys() - a.keys()),
            "removed": sorted(a.keys() - b.keys()),
            "changed": [{"id": key, "before": a[key], "after": b[key]}
                        for key in sorted(a.keys() & b.keys()) if a[key] != b[key]],
        }
    result["environment"] = {"before": asdict(left.environment), "after": asdict(right.environment)}
    return result
