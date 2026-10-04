"""Present a saved trait and its frozen source context for the report disclosure."""
import json

from swarm_lens.core.models import DomainError
from swarm_lens.observability.mast.method import assets
from swarm_lens.observability.mast.trace import decode_trace


def present_trait_details(service, job_id, code):
    job = service.jobs.get(job_id)
    output = job.get("analysis", {}).get("output", {}).get("report") or {}
    label = next((item for item in output.get("labels", []) if item["code"] == code), None)
    if label is None:
        raise DomainError("This report has no saved judgment for that trait")
    category = next(item for item in assets()["categories"] if item["code"] == code)
    evidence = output.get("evidence", {}).get("traits", {}).get(code, {})
    result = {**label, "branch_id": job["branch_id"], "cursor": job["cursor"],
              "status": evidence.get("status", "unavailable"),
              "explanation": evidence.get("explanation", ""),
              "definition": category["definition"], "summary": output.get("summary", ""),
              "occurrences": [], "notice": ""}
    if category["label"] != category["definition_label"]:
        result["definition"] = (f"Upstream definition is labeled {category['definition_label']}; "
                                "the report retains the notebook's question code.\n\n" + category["definition"])
    if result["status"] == "unavailable":
        result["notice"] = ("Evidence unavailable for this saved judgment. "
                            "Run a new analysis to generate per-trait details. "
                            "The overall summary below is shared by all traits.")
    elif result["status"] == "not_localized":
        result["notice"] = "No supported occurrence was localized for this judgment. Review the explanation."
    if not evidence.get("occurrences"):
        return result
    # Use the exact analyzed input, not a later branch head or a browser cursor.
    trace = json.loads(service.artifacts.get(job["trace_artifact"]))["text"]
    events = decode_trace(json.loads(trace))["events"]
    for occurrence in evidence["occurrences"]:
        supporting = set(occurrence["supporting_event_ids"])
        counter = set(occurrence["counterevidence_event_ids"])
        context = []
        for event in events:
            if not occurrence["start_position"] <= event["position"] <= occurrence["end_position"]:
                continue
            data = event["data"]
            role = "supporting" if event["event_id"] in supporting else "counterevidence" if event["event_id"] in counter else "context"
            context.append({"event_id": event["event_id"], "position": event["position"],
                            "at": event["at"], "kind": event["kind"], "role": role,
                            "message_id": data.get("id") if event["kind"] == "message.created" else None,
                            "sender": data.get("sender_name") or data.get("sender_id") or data.get("agent_id"),
                            "text": data.get("content") or json.dumps(data, ensure_ascii=False, indent=2)})
        result["occurrences"].append({**occurrence, "events": context})
    return result
