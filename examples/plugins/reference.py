"""Reference plugins to copy: an Analyzer and an Intervention, registered by one factory.

    swarm-lens --data data --plugin examples.plugins.reference:create
"""
from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.core.models import DomainError, Fact
from swarm_lens.plugins import Annotation, Metric


class MessageLength:
    """Characters per message, per agent, and a marker on each message longer than a threshold."""
    id, version = "message-length", "1.0.0"
    title = "Message length"
    description = "Characters per message, with long messages marked."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        long_message: int = Field(500, ge=1, title="Long message", description="Mark messages with more characters")

    def analyze(self, view, start, end, params):
        for event in view.events(start, end, kind="message.created"):
            length = len(event.data["content"])
            yield Metric(event.position, "characters", length, event.data.get("sender_id"))
            if length > params.long_message:
                yield Annotation(event.position, event.position, "Long message", event.data.get("sender_id"),
                                 score=length / params.long_message, data={"characters": length},
                                 cited_event_ids=(event.id,))


class SilenceAgent:
    """Fork with one agent removed: it keeps its history but leaves its channels and sends nothing more."""
    id, version = "silence-agent", "1.0.0"
    title = "Silence agent"
    description = "Remove an agent from the conversation at the selected event."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent_id: str = Field(min_length=1, title="Agent ID")

    def intervene(self, view, at, params):
        state = view.state_at(at)
        agent = state.agents.get(params.agent_id)
        if agent is None or not agent.active:
            raise DomainError(f"No active agent {params.agent_id!r} at event {at}")
        return [Fact("agent.removed", {"id": params.agent_id}, state.occurred_at)]


def create(services):
    return [MessageLength(), SilenceAgent()]
