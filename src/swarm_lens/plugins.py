from collections import Counter


class ActivityPlugin:
    id = "activity"
    version = "1.0.0"

    def run(self, context, config):
        state = context.state
        by_agent = Counter(message.sender_id for message in state.messages.values() if message.sender_id)
        by_channel = Counter(message.channel_id for message in state.messages.values())
        return {
            "messages": len(state.messages), "tool_calls": len(state.tools),
            "memory_items": len(state.memories),
            "active_agents": sum(agent.active for agent in state.agents.values()),
            "messages_by_agent": dict(by_agent), "messages_by_channel": dict(by_channel),
            "tool_errors": sum(call.status == "failed" for call in state.tools.values()),
            "interpretation": "Descriptive activity counts; no alignment classification is inferred.",
        }
