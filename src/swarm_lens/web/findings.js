import { api, post, formatNumber } from "./ui.js";

export const NO_FINDINGS = Object.freeze({ series: [], annotations: [] });
export const ACTIVE_JOB = new Set(["queued", "running"]);
const POLL_MS = 1000;
const MAX_POINTS = 2000;

const jobKey = (job) => `${job.plugin_id}/${job.id}`;

// Plugins, their findings per branch, and analysis jobs. `jobChanged(job)` reports every status
// update; `jobFinished(job)` fires once a watched job leaves queued/running, after its branch's
// findings were invalidated. `report(error)` receives polling failures.
export class PluginClient {
  constructor({ jobChanged, jobFinished, report }) {
    this.plugins = [];
    this.findings = new Map();
    this.jobs = new Map();
    this.watched = new Set();
    this.callbacks = { jobChanged, jobFinished, report };
  }

  async loadPlugins() {
    this.plugins = (await api("/plugins")).plugins;
  }

  withCapability(...capabilities) {
    return this.plugins.filter((plugin) => capabilities.some((capability) => plugin.capabilities.includes(capability)));
  }

  title(pluginId) {
    return this.plugins.find((plugin) => plugin.id === pluginId)?.title || pluginId;
  }

  // Findings of the latest completed analysis per plugin, for the whole branch.
  load(branchId) {
    if (!this.findings.has(branchId)) {
      const id = encodeURIComponent(branchId);
      const request = Promise.all([api(`/branches/${id}/series?max_points=${MAX_POINTS}`), api(`/branches/${id}/annotations`)])
        .then(([{ series }, { annotations }]) => ({ series, annotations }));
      request.catch(() => this.findings.delete(branchId));
      this.findings.set(branchId, request);
    }
    return this.findings.get(branchId);
  }

  jobsFor(branchId) {
    return this.jobs.get(branchId) || [];
  }

  // Loads the branch's jobs and resumes polling those still running, e.g. after a page reload.
  async loadJobs(branchId) {
    const { jobs } = await api(`/branches/${encodeURIComponent(branchId)}/analyses`);
    this.jobs.set(branchId, jobs);
    jobs.filter((job) => ACTIVE_JOB.has(job.status)).forEach((job) => this.watch(job));
    return jobs;
  }

  async startAnalysis(branchId, body) {
    const job = await post(`/branches/${encodeURIComponent(branchId)}/analyses`, body);
    this.watch(job);
    return job;
  }

  intervene(branchId, pluginId, body) {
    return post(`/branches/${encodeURIComponent(branchId)}/interventions/${encodeURIComponent(pluginId)}`, body);
  }

  record(job) {
    const jobs = this.jobsFor(job.branch_id).filter((item) => jobKey(item) !== jobKey(job));
    this.jobs.set(job.branch_id, [job, ...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at)));
    this.callbacks.jobChanged(job);
  }

  watch(job) {
    this.record(job);
    if (this.watched.has(jobKey(job))) return;
    this.watched.add(jobKey(job));
    const poll = async () => {
      try {
        const next = await api(`/analyses/${encodeURIComponent(job.plugin_id)}/${encodeURIComponent(job.id)}`);
        this.record(next);
        if (ACTIVE_JOB.has(next.status)) return setTimeout(poll, POLL_MS);
        this.watched.delete(jobKey(job));
        this.findings.delete(next.branch_id);
        this.callbacks.jobFinished(next);
      } catch (error) {
        this.watched.delete(jobKey(job));
        this.callbacks.report(new Error(`Lost track of the ${this.title(job.plugin_id)} analysis: ${error.message}`, { cause: error }));
      }
    };
    setTimeout(poll, POLL_MS);
  }
}

export const eventSpan = ({ seq_from, seq_to }) =>
  seq_from === seq_to ? `Event ${formatNumber(seq_from)}` : `Events ${formatNumber(seq_from)}–${formatNumber(seq_to)}`;

// What plugins found at one event: annotations whose span contains it or that cite it, and the
// metric values recorded for its position.
export function findingsForEvent({ series, annotations }, event) {
  return {
    annotations: annotations.filter((annotation) => (annotation.seq_from <= event.position && event.position <= annotation.seq_to)
      || annotation.cited_event_ids?.includes(event.id)),
    metrics: series.flatMap((track) => {
      const point = track.points.find(([seq]) => seq === event.position);
      return point ? [{ plugin: track.plugin, name: track.name, agent_id: track.agent_id, value: point[1] }] : [];
    }),
  };
}
