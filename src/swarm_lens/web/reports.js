import { $, el, button, api, toast, failure } from "./ui.js";
import { openDialog } from "./dialog.js";
import { workspaceURL } from "./workspace.js";

// The Reports tab lists saved analysis jobs from every plugin that contributes a report source.

const active = new Set(["queued", "running"]);
const statuses = {
  completed: { label: "Completed", tone: "accent" },
  needs_review: { label: "Needs review", tone: "" },
  queued: { label: "Queued", tone: "" },
  running: { label: "Analyzing", tone: "" },
  failed: { label: "Failed", tone: "danger" },
  interrupted: { label: "Interrupted", tone: "" },
};

export const isActive = (job) => active.has(job.status);

export function statusBadge(status) {
  const { label, tone } = statuses[status] || { label: status, tone: "" };
  return el("span", `badge ${tone}`.trim(), label);
}

function relativeDate(iso) {
  const seconds = (new Date(iso) - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]];
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export const longDate = (iso) => new Date(iso).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" });

export function reportURL(job) {
  return workspaceURL({ branchId: job.branch_id, cursor: job.cursor, view: "reports", report: job.id, plugin: job.plugin_id });
}

export function emptyState(message, ...actions) {
  const node = el("div", "report-empty");
  node.append(el("p", "", message), ...actions);
  return node;
}

export function details(title, content, className = "") {
  const node = el("details", `report-details ${className}`.trim());
  node.append(el("summary", "", title), typeof content === "string" ? el("pre", "metadata-box", content) : content);
  return node;
}

export function statTile(value, label, alarming = false) {
  const node = el("div", "report-stat");
  node.append(el("strong", alarming ? "alarming" : "", value), el("span", "", label));
  return node;
}

export function progressRow(text) {
  const row = el("div", "report-progress");
  row.setAttribute("role", "status");
  row.textContent = text;
  return row;
}

function downloadResult(job) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(job, null, 2)], { type: "application/json" }));
  const link = el("a");
  link.href = url;
  link.download = `${job.plugin_id}-${job.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyLink(job) {
  const url = reportURL(job);
  try {
    await navigator.clipboard.writeText(url);
    toast("Link copied");
  } catch {
    toast(`Copy blocked by the browser. Link: ${url}`, 10000);
  }
}

export function reportHeader(title, subtitle, job, host) {
  const header = el("header", "report-header");
  const heading = el("div", "report-title");
  const name = el("h2", "", title);
  name.append(statusBadge(job.status));
  heading.append(name, el("p", "muted", subtitle));
  const actions = el("div", "report-actions");
  actions.append(
    button("View snapshot", () => host.showSnapshot(job.branch_id, job.cursor).catch(failure), "ghost"),
    button("Copy link", () => copyLink(job), "ghost"),
    button("Download JSON", () => downloadResult(job), "ghost"));
  header.append(heading, actions);
  return header;
}

// A start dialog whose free preview reruns on every input change; Analyze waits for a ready preview.
// `check()` resolves to { ready, message }, where message is text or a node.
export function openAnalysisDialog({ title, fields, note, check, start }) {
  let ready = false;
  let generation = 0;
  const status = el("div", "analysis-check");
  status.setAttribute("role", "status");
  const root = el("div", "analysis-dialog");
  root.append(...fields, status, el("p", "muted", note));
  openDialog(title, root, { confirm: "Analyze", pending: "Starting…",
    submit: async () => {
      if (!ready) throw new Error("Wait for the check to finish.");
      await start();
    },
  });
  const confirm = $("#confirm-dialog");
  const isCurrent = (ticket) => ticket === generation && root.isConnected && $("#dialog").open;
  const show = (message, state) => { status.replaceChildren(message); status.dataset.state = state; };
  async function refresh() {
    const ticket = ++generation;
    ready = false;
    confirm.disabled = true;
    show("Checking…", "pending");
    try {
      const result = await check();
      if (!isCurrent(ticket)) return;
      ready = result.ready;
      show(result.message, ready ? "ready" : "error");
    } catch (error) {
      if (!isCurrent(ticket)) return;
      show(error.message, "error");
    }
    if (isCurrent(ticket)) confirm.disabled = !ready;
  }
  refresh();
  return refresh;
}

// A source is { plugin, title, prefix, render(root, job), analyze() }; its jobs come from `GET {prefix}/analyses`.
export class Reports {
  constructor(host) {
    this.host = host;
    this.sources = new Map();
    this.root = null;
    this.generation = 0;
    this.timer = null;
  }

  add(source) {
    this.sources.set(source.plugin, source);
    if (this.root) return;
    this.root = this.host.registerView({ id: "reports", title: "Reports",
      onShow: (params) => this.show(params), onHide: () => this.stop() });
    this.root.classList.add("reports-view");
  }

  stop() {
    this.generation++;
    clearTimeout(this.timer);
  }

  analyzeButtons(className) {
    return [...this.sources.values()].map((source) => button(`Analyze with ${source.title}`, source.analyze, className));
  }

  newReportMenu(anchor) {
    this.host.openMenu([{ heading: "New report" },
      ...[...this.sources.values()].map((source) => ({ label: `${source.title}…`, onClick: source.analyze }))], { anchor });
  }

  async jobs(branchId) {
    const lists = await Promise.all([...this.sources.values()].map(async (source) => {
      const { jobs } = await api(`${source.prefix}/analyses?branch_id=${encodeURIComponent(branchId)}`);
      return jobs;
    }));
    return lists.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  listItem(job, selected) {
    const link = el("a", "report-list-item");
    link.href = reportURL(job);
    if (selected) link.setAttribute("aria-current", "page");
    link.onclick = (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      this.host.openView("reports", { report: job.id, plugin: job.plugin_id });
    };
    const text = el("span", "report-list-text");
    text.append(el("strong", "", this.sources.get(job.plugin_id).title),
      el("span", "muted", `Events 1–${job.cursor} · ${relativeDate(job.created_at)}`));
    link.append(text, statusBadge(job.status));
    return link;
  }

  async show(params = {}) {
    const ticket = ++this.generation;
    const { root, host } = this;
    const selection = host.selection();
    if (!selection) return root.replaceChildren(emptyState("Select a run to see its reports."));
    const sidebar = el("nav", "report-list");
    sidebar.setAttribute("aria-label", "Saved reports");
    sidebar.dataset.viewScroll = "";
    const report = el("article", "report");
    report.dataset.viewScroll = "";
    let selected = params.report && { id: params.report, plugin: params.plugin };
    let rendered = "";

    const refresh = async () => {
      const jobs = await this.jobs(selection.branchId);
      if (ticket !== this.generation) return;
      selected ||= jobs[0] && { id: jobs[0].id, plugin: jobs[0].plugin_id };
      let job = selected && jobs.find((item) => item.id === selected.id);
      // Direct links remain usable after a report falls outside the latest 50 jobs.
      if (!job && selected) job = await this.fetchJob(selected);
      if (ticket !== this.generation) return;
      if (!job) return root.replaceChildren(emptyState("No reports for this branch yet.", ...this.analyzeButtons("primary")));
      if (job.branch_id !== selection.branchId) throw new Error("This report belongs to a different branch.");
      if (!jobs.some((item) => item.id === job.id)) jobs.push(job);
      if (!sidebar.isConnected) root.replaceChildren(sidebar, report);
      const listHeader = el("div", "report-list-header");
      const create = button("New", (event) => this.newReportMenu(event.currentTarget), "ghost");
      create.setAttribute("aria-haspopup", "menu");
      create.setAttribute("aria-expanded", "false");
      listHeader.append(el("span", "section-title", "Reports"), create);
      sidebar.replaceChildren(listHeader, ...jobs.map((item) => this.listItem(item, item.id === job.id)));
      host.setViewParams("reports", { report: job.id, plugin: job.plugin_id });
      const source = this.sources.get(job.plugin_id);
      report.setAttribute("aria-label", `${source.title} report`);
      const signature = JSON.stringify(job);
      if (signature !== rendered) { source.render(report, job); rendered = signature; }
      clearTimeout(this.timer);
      if (jobs.some(isActive)) this.timer = setTimeout(() => refresh().catch(showError), 1500);
    };

    const showError = (error) => {
      if (ticket !== this.generation) return;
      const message = emptyState(error.message, button("Retry", () => refresh().catch(showError)));
      message.querySelector("p").className = "report-error";
      if (params.report) message.append(button("All reports", () => host.openView("reports"), "ghost"));
      root.replaceChildren(message);
    };

    root.replaceChildren(emptyState("Loading reports…"));
    await refresh().catch(showError);
  }

  async fetchJob({ id, plugin }) {
    const source = this.sources.get(plugin);
    if (!source) throw new Error("This report comes from a plugin that is not installed.");
    return api(`${source.prefix}/analyses/${encodeURIComponent(id)}`);
  }
}
