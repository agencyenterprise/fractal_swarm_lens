"""The numbered event text that every method reads. Positions are the citation keys."""
import json

# Prompt captures repeat context that the run already records as messages and memory.
PROMPT_CAPTURE_TYPES = {"model_input"}


def _compact(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _one_line(text):
    """Escape line breaks in every field, so an event marker can only start a line at a real event."""
    return text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _reported_tool(d):
    """A tool call reconstructed from what an agent wrote: reported, never a verified execution."""
    metadata = d["metadata"]
    observation = (f"reported observation: {_compact(metadata['reported_observation'])}" if metadata.get("observed")
                   else "execution not observed")
    # The line follows its source message; ids are never shown, so planted and background calls read alike.
    return f"tool {d['tool_name']} reported by {d['agent_id']}: args={_compact(d.get('arguments'))}; {observation}"


def render_event(event):
    """Return one line for an event, or None when the event only repeats recorded context. A reconstructed
    tool call renders once, at its started event; its other events keep their positions but render nothing."""
    d, kind = event.data, event.kind
    metadata = d.get("metadata") or {}
    if d.get("type") in PROMPT_CAPTURE_TYPES or metadata.get("type") in PROMPT_CAPTURE_TYPES:
        return None
    if kind.startswith("tool.") and metadata.get("reconstructed"):
        if kind != "tool.started":
            return None
        body = _reported_tool(d)
    elif kind == "message.created":
        sender = d.get("sender_name") or d.get("sender_id") or d.get("role")
        body = f"message from {sender} in channel {d['channel_id']}: {_compact(d['content'])}"
    elif kind == "memory.written":
        body = f"memory {d['id']} written by {d.get('owner_id') or 'shared'}: {_compact(d['content'])}"
    elif kind.startswith("tool."):
        outcome = d.get("error") or _compact(d.get("result"))
        body = f"tool {d['tool_name']} by {d['agent_id']} ({d['status']}) args={_compact(d.get('arguments'))} result={outcome}"
    elif kind == "observation.recorded":
        body = f"observation {d['type']}" + (f" by {d['agent_id']}" if d.get("agent_id") else "") + \
               (f": {_compact(d['content'])}" if d.get("content") else "")
    elif kind.startswith("agent."):
        body = (f"{kind} {d['name']} ({d['id']}) model={d.get('model')} active={d.get('active')}"
                + (f" system prompt: {_compact(d['system_prompt'])}" if d.get("system_prompt") else ""))
    elif kind.startswith("channel."):
        body = f"{kind} {d['name']} ({d['id']}) members={', '.join(d.get('members', []))}"
    elif kind == "environment.updated":
        body = f"environment task: {_compact(d.get('task', ''))} | goal: {_compact(d.get('goal', ''))}"
    else:
        body = f"{kind}: {_compact(d)}"
    return f"[{event.position}] {_one_line(body)}"


def render_history(history):
    """Return (position, line) pairs in history order."""
    return [(event.position, line) for event in history if (line := render_event(event)) is not None]
