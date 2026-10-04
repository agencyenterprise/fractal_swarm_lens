"""Durable on-demand jobs; the input is frozen before scheduling the judge."""
from dataclasses import asdict
import hashlib
import json

from swarm_lens.application.plugins import record_failure
from swarm_lens.core.findings import encode_output
from swarm_lens.core.models import DomainError, new_id, utc_now
from .method import MastPlugin


def upgrade_job(record, event_ids):
    """A job saved before plugin version 0.4.0 in the new shape: its assessment becomes the report, its
    evidence becomes annotations, and the analyzed range is 1..cursor. `event_ids` are the analyzed events."""
    record = {**record, "start": 1, "end": record["cursor"]}
    analysis = record.get("analysis")
    if analysis and "format" not in analysis["output"]:
        items = MastPlugin.results(analysis["output"], 1, record["cursor"])
        record["analysis"] = {**analysis, "start": 1, "end": record["cursor"],
                              "output": encode_output(items, MastPlugin.id, 1, record["cursor"], event_ids)}
    return record


class MastService:
    def __init__(self, plugins, plugin, artifacts):
        self.plugins, self.plugin, self.artifacts = plugins, plugin, artifacts
        self.framework, self.jobs = plugins.framework, plugins.jobs(plugin.id, "MAST")

    def preview(self, branch_id, cursor, completeness):
        history = self.framework.history(branch_id, cursor)
        _, _, summary = self.plugin.prepare_input(history, {"completeness": completeness})
        description = self.plugin.judge.describe()
        if not description["ready"]:
            summary.update(can_analyze=False, reason=description["reason"])
        return {**summary, "branch_id": branch_id, "cursor": cursor, "judge": description}

    def submit(self, branch_id, cursor, completeness):
        description = self.plugin.judge.describe()
        if not description["ready"]:
            raise DomainError(description["reason"])
        history = self.framework.history(branch_id, cursor)
        config = {"completeness": completeness}
        trace, prompt, summary = self.plugin.prepare_input(history, config)
        if not summary["can_analyze"]:
            raise DomainError(summary["reason"])
        input_digest = hashlib.sha256(json.dumps([asdict(e) for e in history], sort_keys=True).encode()).hexdigest()
        record = {"id": new_id(), "plugin_id": self.plugin.id, "plugin_version": self.plugin.version,
                  "branch_id": branch_id, "start": 1, "end": cursor, "cursor": cursor, "created_at": utc_now(), "status": "queued",
                  "config": config, "input_digest": input_digest, "judge": description, "input_summary": summary,
                  "trace_artifact": self.artifacts.put(json.dumps({"text": trace}, ensure_ascii=False).encode()),
                  "prompt_artifact": self.artifacts.put(json.dumps({"text": prompt}, ensure_ascii=False).encode())}
        self.jobs.create(record)
        return record

    def execute(self, job_id):
        record = self.jobs.claim(job_id)
        if record is None:
            return
        try:
            trace = json.loads(self.artifacts.get(record["trace_artifact"]))["text"]
            prompt = json.loads(self.artifacts.get(record["prompt_artifact"]))["text"]
            output = self.plugin.evaluate(trace, prompt)
            output["input_summary"] = record.get("input_summary")
            cursor = record["cursor"]
            analysis = self.plugins.save(self.plugin, self.plugins.view(record["branch_id"], cursor), 1, cursor,
                                         record["config"], self.plugin.results(output, 1, cursor))
            record.update(status="completed" if output["parse_status"] == "complete" else "needs_review",
                          analysis=analysis)
        except Exception as exc:
            record_failure(record, exc, "MAST analysis failed; no negative classifications were inferred.")
        record["finished_at"] = utc_now()
        self.jobs.update(record)
