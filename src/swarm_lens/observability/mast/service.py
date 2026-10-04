"""Durable on-demand jobs; the input is frozen before scheduling the judge."""
from dataclasses import asdict
import hashlib
import json

from swarm_lens.core.findings import encode_output
from swarm_lens.core.models import DomainError, new_id, utc_now
from .errors import provider_failure
from .method import MastPlugin


def upgrade_job(record, event_ids):
    """Wrap a job from the separate store in the shared findings format, keeping chunk artifacts intact.

    Its assessment becomes the report, evidence becomes annotations, and the range is 1..cursor.
    `event_ids` are the analyzed events.
    """
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
        config = {"completeness": completeness, "workers": self.plugin.workers}
        prepared = self.plugin.prepare_analysis(history, config)
        trace, prompt, summary = prepared["trace"], prepared["prompt"], prepared["summary"]
        if not summary["can_analyze"]:
            raise DomainError(summary["reason"])
        input_digest = hashlib.sha256(json.dumps([asdict(e) for e in history], sort_keys=True).encode()).hexdigest()
        record = {"id": new_id(), "plugin_id": self.plugin.id, "plugin_version": self.plugin.version,
                  "branch_id": branch_id, "start": 1, "end": cursor, "cursor": cursor, "created_at": utc_now(), "status": "queued",
                  "config": config, "input_digest": input_digest, "judge": description, "input_summary": summary,
                  "trace_artifact": self.artifacts.put(json.dumps({"text": trace}, ensure_ascii=False).encode()),
                  "prompt_artifact": self.artifacts.put(json.dumps({"text": prompt}, ensure_ascii=False).encode())}
        if prepared["chunks"]:
            record["chunk_plan_artifact"] = self.artifacts.put(json.dumps(prepared["chunks"], ensure_ascii=False).encode())
            record["progress"] = {"phase": "queued", "completed_chunks": 0, "total_chunks": len(prepared["chunks"])}
        self.jobs.create(record)
        return record

    def execute(self, job_id):
        record = self.jobs.claim(job_id)
        if record is None:
            return
        try:
            trace = json.loads(self.artifacts.get(record["trace_artifact"]))["text"]
            prompt = json.loads(self.artifacts.get(record["prompt_artifact"]))["text"]
            if record.get("chunk_plan_artifact"):
                from .chunking import evaluate_chunks
                chunks = json.loads(self.artifacts.get(record["chunk_plan_artifact"]))

                def progress(**values):
                    record["progress"] = values
                    self.jobs.update(record)

                def save_stage(stage):
                    artifact = self.artifacts.put(json.dumps(stage, ensure_ascii=False).encode())
                    summary = {k: v for k, v in stage.items() if k not in {"trace", "prompt", "output"}}
                    summary.update(artifact=artifact, parse_status=stage["output"]["parse_status"],
                                   summary=stage["output"]["summary"])
                    record.setdefault("stages", []).append(summary)
                    record["stages"].sort(key=lambda s: (s["phase"] != "chunk", s["index"]))
                    self.jobs.update(record)
                    return summary

                output = evaluate_chunks(self.plugin, trace, chunks, progress, save_stage)
            else:
                output = self.plugin.evaluate(trace, prompt)
            output["input_summary"] = record.get("input_summary")
            cursor = record["cursor"]
            analysis = self.plugins.save(self.plugin, self.plugins.view(record["branch_id"], cursor), 1, cursor,
                                         record["config"], self.plugin.results(output, 1, cursor))
            record.update(status="completed" if output["parse_status"] == "complete" else "needs_review",
                          analysis=analysis)
        except Exception as exc:
            message, diagnostic = provider_failure(exc)
            if record.get("stages"):
                message += " Completed chunk/reconciliation stages are retained below; no combined report was published."
            record.update(status="failed", error=message,
                          error_type=type(exc).__name__, provider_error=diagnostic)
        record["finished_at"] = utc_now()
        self.jobs.update(record)
