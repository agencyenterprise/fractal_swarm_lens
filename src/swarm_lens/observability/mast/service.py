"""Durable on-demand jobs; the input is frozen before scheduling the judge."""
from dataclasses import asdict
import hashlib
import json

from swarm_lens.adapters.jobs import record_failure
from swarm_lens.core.models import DomainError, new_id, utc_now


class MastService:
    def __init__(self, framework, plugin, jobs, artifacts):
        self.framework, self.plugin, self.jobs, self.artifacts = framework, plugin, jobs, artifacts

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
                  "branch_id": branch_id, "cursor": cursor, "created_at": utc_now(), "status": "queued",
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
            analysis = {key: record[key] for key in (
                "id", "plugin_id", "plugin_version", "branch_id", "cursor", "created_at", "config", "input_digest",
            )}
            analysis["output"] = output
            self.framework.store.save_analysis(analysis)
            record.update(status="completed" if output["parse_status"] == "complete" else "needs_review",
                          analysis=analysis)
        except Exception as exc:
            record_failure(record, exc, "MAST analysis failed; no negative classifications were inferred.")
        record["finished_at"] = utc_now()
        self.jobs.update(record)
