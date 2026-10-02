import bisect
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from swarm_lens import Agent, Channel, Fact, Memory, Message, ToolCall


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


class VillageSource:
    """AI Village interpretation lives entirely in the application example."""

    def __init__(self, root: Path, artifacts):
        self.root, self.artifacts = root, artifacts
        self.selection = self.load("selection")

    def load(self, name):
        return json.loads((self.root / f"{name}.json").read_text())

    def source_ref(self, table, row):
        return {"origin": "dataset", "repository": self.selection["repository"],
                "revision": self.selection["revision"], "table": table, "row_id": row["id"]}

    def fact(self, kind, data, table, row, at=None):
        source = self.source_ref(table, row)
        identity = str(uuid5(NAMESPACE_URL, f"{source['repository']}:{source['revision']}:{table}:{row['id']}:{kind}"))
        return Fact(kind, data, timestamp(at or row["created_at"]), source, identity)

    def artifact(self, value):
        if value is None:
            return None
        return self.artifacts.put(json.dumps(value, ensure_ascii=False).encode())

    def facts(self):
        start = self.selection["start"]
        events = sorted(self.load("events-selected"), key=lambda row: row["event_index"])
        messages = {row["id"]: row for row in self.load("chat_messages-selected")}
        sessions = {row["id"]: row for row in self.load("computer_use_sessions-selected")}
        turns = self.load("computer_use_turns-selected")
        memories = self.load("agent_memories-selected")
        agents = {row["id"]: row for row in self.load("agents")}
        agent_ids = {row["agent_speaker_id"] for row in messages.values() if row["agent_speaker_id"]}
        agent_ids |= {row["agent_id"] for row in sessions.values()} | {row["agent_id"] for row in memories}
        goal = next(row for row in self.load("village_goals") if row["id"] == self.selection["goal_id"])
        yield self.fact("environment.updated", {
            "task": "Elect a village leader", "goal": goal["goal"],
            "metadata": {**self.selection, "goal_start": goal["start_time"], "goal_end": goal["end_time"],
                         "session_count": len(sessions), "system_prompts": "not exported", "model_internals": "not exported"},
        }, "village_goals", goal, start)
        for agent_id in sorted(agent_ids, key=lambda aid: agents[aid]["name"]):
            row = agents[agent_id]
            agent = Agent(row["id"], row["name"], row["model_string"], metadata={
                "model_provenance": "roster at export; historical per-call outputs retained separately",
                "initial_context": "partially observed", "system_prompt_available": False,
            })
            yield self.fact("agent.added", asdict(agent), "agents", row, start)
        room_ids = {row["room_id"] for row in messages.values()}
        for row in self.load("chat_rooms"):
            if row["id"] in room_ids:
                yield self.fact("channel.created", asdict(Channel(row["id"], row["name"], metadata={
                    "membership": "not inferred; graph edges show recorded messages",
                })), "chat_rooms", row, start)

        anchors = [timestamp(row["created_at"]) for row in events]
        pending = []
        for index, row in enumerate(events):
            data, action = row["data"], row["data"]["actionType"]
            raw = self.artifact(data.get("output"))
            metadata = {"source_event_index": row["event_index"], "model_output_artifact": raw}
            message = messages.get(data.get("messageId"))
            if action in ("AGENT_TALK", "USER_TALK") and message:
                metadata.update({"source_message_id": message["id"], "message_created_at": timestamp(message["created_at"])})
                value = Message(message["id"], message["room_id"], message["content"],
                                message["agent_speaker_id"], data.get("speakerName"),
                                "agent" if message["speaker_type"] == "agent" else "human", metadata=metadata)
                fact = self.fact("message.created", asdict(value), "events", row)
                fact.source["message_id"] = message["id"]
            else:
                agent_id = data.get("agentId") or (data.get("speakerId") if data.get("speakerId") in agents else None)
                fact = self.fact("observation.recorded", {
                    "type": action, "agent_id": agent_id,
                    "content": data.get("summary") or data.get("sessionGoal") or data.get("query") or action.replace("_", " ").title(),
                    "metadata": {**metadata, "session_id": data.get("computerUseSessionId"), "original": {k: v for k, v in data.items() if k != "output"}},
                }, "events", row)
            pending.append(((index, 0, ""), fact))
        linked_messages = {row["data"].get("messageId") for row in events}
        for row in messages.values():
            if row["id"] not in linked_messages:
                raise ValueError(f"Chat message {row['id']} has no timeline anchor in this selection")
        for row in memories:
            value = Memory("operational:" + row["agent_id"], row["content"], row["agent_id"], metadata={"source_revision_id": row["id"]})
            fact = self.fact("memory.written", asdict(value), "agent_memories", row)
            pending.append(((bisect.bisect_right(anchors, fact.occurred_at), -1, fact.occurred_at + row["id"]), fact))
        for row in turns:
            session = sessions[row["session_id"]]
            metadata = {"session_id": session["id"], "session_goal": session["session_goal"],
                        "model_output_artifact": self.artifact(row["agent_messages"]),
                        "screenshot_available": False, "screenshot_redacted": row.get("screenshot_is_redacted", False)}
            action = row["agent_action"]
            if action:
                value = ToolCall(row["id"], session["agent_id"],
                                 action.get("action") or ("bash" if "command" in action else "computer"),
                                 action, row.get("output"), row.get("error"),
                                 "failed" if row.get("error") else "completed", metadata)
                fact = self.fact("tool.recorded", asdict(value), "computer_use_turns", row)
            else:
                fact = self.fact("observation.recorded", {
                    "type": "MODEL_TURN", "agent_id": session["agent_id"],
                    "content": "Model response without an executed computer action", "metadata": metadata,
                }, "computer_use_turns", row)
            pending.append(((bisect.bisect_right(anchors, fact.occurred_at), -1, fact.occurred_at + row["id"]), fact))
        for _, fact in sorted(pending, key=lambda item: item[0]):
            yield fact
