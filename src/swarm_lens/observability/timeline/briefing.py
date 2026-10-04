"""The starting pack each method receives: the goals in effect, the agents' roles, and an activity map.

Everything here is computed from events without any model call. A section briefing describes only
the goals and agents that matter for that section, so long multi-team runs stay compact.
"""
from collections import Counter
import json

ROLE_PREVIEW = 600
MAX_AGENTS = 60
MAX_GOALS = 30
MAX_CHANGES = 50


def goals_in_effect(history, start, end):
    """The goal active when the span starts, plus every goal set inside it."""
    goals = [{"position": e.position, "task": e.data.get("task", ""), "goal": e.data.get("goal", "")}
             for e in history if e.kind == "environment.updated" and e.position <= end
             and (e.data.get("task") or e.data.get("goal"))]
    before = [g for g in goals if g["position"] < start][-1:]
    return before + [g for g in goals if g["position"] >= start]


def agent_roles(history, agent_ids=None):
    roles = {}
    for event in history:
        if event.kind in ("agent.added", "agent.updated") and (agent_ids is None or event.data["id"] in agent_ids):
            prompt = event.data.get("system_prompt") or ""
            roles[event.data["id"]] = {"name": event.data["name"], "model": event.data.get("model"),
                                       "role": prompt[:ROLE_PREVIEW] + ("…" if len(prompt) > ROLE_PREVIEW else "")}
    return roles


def acting_agents(events):
    return {e.data.get("sender_id") or e.data.get("agent_id") or e.data.get("owner_id") for e in events} - {None}


def tool_calls(events):
    """Tool call id -> the call's fields merged over its events, so each call counts once."""
    calls = {}
    for event in events:
        if event.kind.startswith("tool."):
            calls[event.data["id"]] = {**calls.get(event.data["id"], {}), **event.data}
    return calls


def tool_activity(events):
    """Reported calls were reconstructed from what agents wrote; recorded calls came from real tool events.
    A reported call is observed when the log holds its (agent-reported) observation; a recorded call is
    observed when it reached a final status."""
    calls = tool_calls(events).values()
    # Many sources write tool use inside messages. Absent tool events mean "not recorded", never "no tool use".
    if not calls:
        return {"tool_events": "none recorded; any tool use appears inside messages"}
    reported = [c for c in calls if (c.get("metadata") or {}).get("reconstructed")]
    recorded = [c for c in calls if not (c.get("metadata") or {}).get("reconstructed")]
    return {"reported_tool_calls": {"calls": len(reported),
                                    "observed": sum(bool(c["metadata"].get("observed")) for c in reported),
                                    "failures": sum(c.get("status") == "failed" for c in reported)},
            "recorded_tool_calls": {"calls": len(recorded),
                                    "observed": sum(c.get("status") in ("completed", "failed") for c in recorded),
                                    "failures": sum(c.get("status") == "failed" for c in recorded)}}


def activity_map(events):
    """Counts that show where activity, errors, and configuration changes concentrate."""
    messages = Counter(e.data.get("sender_id") or e.data.get("role") for e in events if e.kind == "message.created")
    changes = [{"position": e.position, "kind": e.kind, "id": e.data.get("id")} for e in events
               if e.kind in ("agent.added", "agent.updated", "agent.removed", "channel.created",
                             "channel.updated", "environment.updated")]
    return {"positions": [events[0].position, events[-1].position],
            "time": [events[0].occurred_at, events[-1].occurred_at],
            "events": len(events), "messages_by_sender": dict(messages.most_common(MAX_AGENTS)),
            **tool_activity(events),
            "configuration_changes": changes[:MAX_CHANGES], "configuration_changes_total": len(changes),
            "interventions": sum(e.source.get("origin") == "intervention" for e in events)}


def capped(items, limit):
    items = list(items)
    return {"shown": items[:limit], "total": len(items)}


def briefing(history, span=None):
    """JSON text placed before the events. `span` = (first, last) position for a section briefing."""
    start, end = span or (history[0].position, history[-1].position)
    events = [e for e in history if start <= e.position <= end]
    known = [e for e in history if e.position <= end]  # never describe a section with later role changes
    roles = agent_roles(known, None if span is None else acting_agents(events))
    return json.dumps({"goals_in_effect": capped(goals_in_effect(history, start, end), MAX_GOALS),
                       "agents": capped(roles.items(), MAX_AGENTS),
                       "activity": activity_map(events)}, ensure_ascii=False)
