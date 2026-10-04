"""Descriptive activity counts as metrics; no alignment classification is inferred."""
from collections import Counter

from swarm_lens.plugins import Metric


class ActivityPlugin:
    id, version = "activity", "2.0.0"
    title = "Activity"
    description = "Running counts of messages per agent and of tool calls and tool errors."

    def analyze(self, view, start, end, params):
        messages, tools, errors = Counter(), Counter(), Counter()
        for event in view.events(start, end):
            data = event.data
            if event.kind == "message.created" and data.get("sender_id"):
                messages[data["sender_id"]] += 1
                yield Metric(event.position, "messages", messages[data["sender_id"]], data["sender_id"])
            if event.kind in ("tool.recorded", "tool.started"):
                tools[data["agent_id"]] += 1
                yield Metric(event.position, "tool_calls", tools[data["agent_id"]], data["agent_id"])
            if event.kind in ("tool.recorded", "tool.completed") and data["status"] == "failed":
                errors[data["agent_id"]] += 1
                yield Metric(event.position, "tool_errors", errors[data["agent_id"]], data["agent_id"])


def create(services):
    return ActivityPlugin()
