import { $, el, button, api, post, toast, failure, formatNumber, tip } from "./ui.js";
import { field, openDialog } from "./dialog.js";
import { mastTraitDetails, modeDefinitions } from "./mast-details.js";
import { workspaceURL } from "./workspace.js";

const prefix = "/plugins/mast";
const active = new Set(["queued", "running"]);
const statuses = {
  completed: { label: "Completed", tone: "accent", tip: "The judge returned a full assessment" },
  needs_review: { label: "Needs review", tone: "", tip: "The judge's response was unfinished; check the raw response" },
  queued: { label: "Queued", tone: "", tip: "Waiting for a free analysis slot" },
  running: { label: "Analyzing", tone: "", tip: "The judge is reading the trace" },
  failed: { label: "Failed", tone: "danger", tip: "The analysis stopped with an error; nothing was classified" },
  interrupted: { label: "Interrupted", tone: "", tip: "The server restarted before this finished; start a new analysis" },
};
const MAST_PAPER = "https://arxiv.org/abs/2503.13657";
const MAST_TAXONOMY = "https://github.com/multi-agent-systems-failure-taxonomy/MAST";
const completenessOptions = [["unknown", "Unknown"], ["complete", "Complete"], ["partial", "Partial"]];

function statusBadge(status) {
  const { label, tone, tip: hint } = statuses[status] || { label: status, tone: "" };
  return tip(el("span", `badge ${tone}`.trim(), label), hint);
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

const longDate = (iso) => new Date(iso).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" });

function details(title, content, className = "mast-details") {
  const node = el("details", className);
  node.append(el("summary", "", title), typeof content === "string" ? el("pre", "metadata-box", content) : content);
  return node;
}

function externalLink(label, url) {
  const link = el("a", "", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

function aboutMast() {
  const node = el("p", "muted mast-about", "MAST is a taxonomy of 14 ways multi-agent LLM systems fail, in 3 categories, "
    + "built by experts from 150 annotated traces. Here an LLM judge reads the run and marks each failure mode present or absent. ");
  node.append(externalLink("Paper", MAST_PAPER), " · ", externalLink("Taxonomy", MAST_TAXONOMY));
  return node;
}

function reportURL(job) {
  return workspaceURL({ branchId: job.branch_id, cursor: job.cursor, view: "mast", report: job.id });
}

function downloadResult(job) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(job, null, 2)], { type: "application/json" }));
  const link = el("a");
  link.href = url;
  link.download = `mast-${job.id}.json`;
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

function reportHeader(job, host) {
  const header = el("header", "mast-report-header");
  const title = el("div", "mast-report-title");
  const heading = el("h2", "", "MAST report");
  heading.append(statusBadge(job.status));
  title.append(heading, aboutMast(), el("p", "muted", `events 1–${job.cursor} · ${job.judge.model} · ${longDate(job.created_at)}`));
  const actions = el("div", "mast-report-actions");
  actions.append(
    tip(button("View snapshot", () => host.showSnapshot(job.branch_id, job.cursor).catch(failure), "ghost"),
      `Open the timeline at event ${job.cursor}, the last event analyzed`),
    tip(button("Copy link", () => copyLink(job), "ghost"), "Copy a link that reopens this report"),
    tip(button("Download JSON", () => downloadResult(job), "ghost"), "The full result with judge response and provenance"));
  header.append(title, actions);
  return header;
}

function outcomeStrip(output) {
  const present = output.labels.filter((label) => label.present === true).length;
  const completed = output.task_completed === null ? "Unparsed" : output.task_completed ? "Yes" : "No";
  const strip = el("div", "mast-outcome");
  const stat = (value, label, alarming, hint) => {
    const node = tip(el("div", "mast-stat"), hint);
    node.append(el("strong", alarming ? "alarming" : "", value), el("span", "", label));
    return node;
  };
  strip.append(stat(completed, "Task completed · judge assessment", output.task_completed === false,
    "The judge's answer to whether the agents completed the task; not checked by a person"),
  stat(String(present), `of ${output.labels.length} failure modes present`, present > 0,
    "MAST failure modes the judge found in this trace"));
  return strip;
}

// Every row expands into the saved explanation and evidence for that trait.
function modeList(labels, job, host, definitions) {
  const list = el("div", "mast-modes");
  list.append(...labels.map((label) => mastTraitDetails(label, job, host, definitions.get(label.code))));
  return list;
}

function failureModes(labels, job, host, definitions) {
  const section = el("section", "mast-failures");
  const present = labels.filter((label) => label.present === true);
  const unparsed = labels.filter((label) => label.present === null);
  const absent = labels.filter((label) => label.present === false);
  const groups = [...new Set(present.map((label) => label.group))];
  for (const group of groups) {
    section.append(el("h3", "section-title", group),
      modeList(present.filter((label) => label.group === group), job, host, definitions));
  }
  if (unparsed.length) {
    section.append(el("h3", "section-title", "Unparsed"), modeList(unparsed, job, host, definitions));
  }
  if (absent.length) section.append(details(`${absent.length} not present`, modeList(absent, job, host, definitions), "mast-details mast-absent"));
  return section;
}

function provenance(job, output) {
  return JSON.stringify({
    branch_id: job.branch_id, cursor: job.cursor, completeness: job.config.completeness,
    input_digest: job.input_digest, plugin_version: job.plugin_version,
    upstream_revision: output.upstream.revision, prompt_sha256: output.prompt_sha256,
    trace_sha256: output.trace_sha256, judge: output.judge, input_summary: job.input_summary,
  }, null, 2);
}

function progressRow(job) {
  const row = el("div", "mast-progress with-spinner");
  row.setAttribute("role", "status");
  row.textContent = `Analyzing events 1–${job.cursor} with ${job.judge.model}…`;
  return row;
}

function renderReport(root, job, host, definitions) {
  root.replaceChildren(reportHeader(job, host));
  if (job.error) root.append(el("p", "mast-error", job.error));
  if (active.has(job.status)) return root.append(progressRow(job));
  if (!job.analysis) return root.append(el("p", "muted", "No assessment was produced."));
  const output = job.analysis.output;
  root.append(outcomeStrip(output));
  if (output.summary) root.append(el("p", "mast-summary", output.summary));
  root.append(failureModes(output.labels, job, host, definitions), el("p", "muted mast-note", "LLM assessment, not human reviewed."));
  const notes = el("div", "mast-notes");
  if (output.warnings.length) notes.append(details("Parsing notes", output.warnings.join("\n")));
  const evidence = output.evidence;
  if (evidence?.warnings?.length) notes.append(details("Evidence notes", evidence.warnings.join("\n")));
  if (evidence) notes.append(details("Evidence provenance", JSON.stringify({ version: evidence.version,
    status: evidence.status, prompt_sha256: evidence.prompt_sha256, judge: evidence.judge }, null, 2)));
  notes.append(details("Raw judge response", output.raw_response),
    details("Upstream taxonomy notes", output.upstream.upstream_notes.join("\n\n")),
    details("Provenance", provenance(job, output)));
  root.append(notes);
}

function reportListItem(job, selected, host) {
  const link = el("a", "mast-list-item");
  link.href = reportURL(job);
  if (selected) link.setAttribute("aria-current", "page");
  link.onclick = (event) => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    host.openView("mast", { report: job.id });
  };
  const text = el("span", "mast-list-text");
  text.append(el("strong", "", `Events 1–${job.cursor}`), el("span", "muted", relativeDate(job.created_at)));
  link.append(text, statusBadge(job.status));
  return link;
}

function emptyState(message, action) {
  const node = el("div", "mast-empty");
  node.append(el("p", "", message));
  if (action) node.append(action);
  return node;
}

function segmented(label, options, value, onChange) {
  const group = el("div", "segmented");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", label);
  const buttons = options.map(([optionValue, optionLabel]) => {
    const control = button(optionLabel, () => {
      for (const other of buttons) other.setAttribute("aria-pressed", String(other === control));
      onChange(optionValue);
    });
    control.setAttribute("aria-pressed", String(optionValue === value));
    return control;
  });
  group.append(...buttons);
  return group;
}

function sizeCheckText(preview) {
  if (!preview.can_analyze) return preview.reason || "This snapshot cannot be analyzed.";
  if (preview.estimated_input_tokens === undefined) return `Fits · ${formatNumber(preview.event_count)} events`;
  return `Fits · ${formatNumber(preview.estimated_input_tokens)} of ${formatNumber(preview.input_token_limit)} tokens`;
}

function analyzeDialog(manifest, selection, host) {
  if (!selection) return toast("Select a run to analyze");
  let completeness = "unknown";
  let ready = false;
  let generation = 0;
  const root = el("div", "mast-dialog");
  const caption = el("span", "mast-field-label", "Trace completeness");
  const status = el("p", "mast-size");
  status.setAttribute("role", "status");
  root.append(caption, segmented("Trace completeness", completenessOptions, completeness, (value) => {
    completeness = value;
    checkInput();
  }), status, el("p", "muted", `Sends events 1–${selection.cursor} to OpenAI ${manifest.judge.model}, then one more request to locate evidence for each trait.`));
  if (!manifest.judge.ready) root.append(el("p", "mast-error", manifest.judge.reason));
  const body = () => ({ branch_id: selection.branchId, cursor: selection.cursor, completeness });
  openDialog("Analyze with MAST", root, { kicker: "", confirm: "Analyze", pending: "Starting…",
    submit: async () => {
      if (!ready) throw new Error("Wait for the size check to finish.");
      await host.openReport(await post(`${prefix}/analyses`, body()));
    },
  });
  const isCurrent = (ticket) => ticket === generation && root.isConnected && $("#dialog").open;
  const show = (text, state) => { status.textContent = text; status.dataset.state = state; };
  async function checkInput() {
    const ticket = ++generation;
    ready = false;
    $("#confirm-dialog").disabled = true;
    show("Checking size…", "pending");
    try {
      const preview = await post(`${prefix}/preview`, body());
      if (!isCurrent(ticket)) return;
      ready = preview.can_analyze;
      show(sizeCheckText(preview), ready ? "ready" : "error");
    } catch (error) {
      if (!isCurrent(ticket)) return;
      show(error.message, "error");
    }
    if (isCurrent(ticket)) $("#confirm-dialog").disabled = !ready;
  }
  if (selection.cursor >= 1) checkInput();
  else {
    show("Select at least one event first.", "error");
    $("#confirm-dialog").disabled = true;
  }
}

function installReportView(manifest, host) {
  let timer;
  let generation = 0;
  let definitions;
  const loadDefinitions = () => (definitions ??= api(`${prefix}/taxonomy`).then(modeDefinitions)
    .catch((error) => { definitions = null; throw error; }));
  const stop = () => { generation++; clearTimeout(timer); };
  const root = host.registerView({ id: "mast", title: "Reports", tip: "MAST failure-mode reports for this branch", onShow: show, onHide: stop });
  root.classList.add("mast-view");
  const analyzeButton = (label, className) =>
    button(label, () => analyzeDialog(manifest, host.selection(), host), className);

  async function show(params = {}) {
    const ticket = ++generation;
    const selection = host.selection();
    if (!selection) return root.replaceChildren(emptyState("Select a run to see its reports."));
    const sidebar = el("nav", "mast-list");
    sidebar.setAttribute("aria-label", "Saved reports");
    sidebar.dataset.viewScroll = "";
    const report = el("article", "mast-report");
    report.setAttribute("aria-label", "MAST report");
    report.dataset.viewScroll = "";
    let selected = params.report;
    let rendered = "";

    async function refresh() {
      const { jobs } = await api(`${prefix}/analyses?branch_id=${encodeURIComponent(selection.branchId)}`);
      if (ticket !== generation) return;
      selected ||= jobs[0]?.id;
      let job = jobs.find((item) => item.id === selected);
      // Direct links remain usable after a report falls outside the latest 50 jobs.
      if (!job && selected) job = await api(`${prefix}/analyses/${encodeURIComponent(selected)}`);
      if (ticket !== generation) return;
      if (!job) {
        return root.replaceChildren(emptyState("No reports for this branch yet.", analyzeButton("Analyze this run", "primary")));
      }
      if (job.branch_id !== selection.branchId) throw new Error("This report belongs to a different branch.");
      if (!jobs.some((item) => item.id === job.id)) jobs.push(job);
      if (!sidebar.isConnected) root.replaceChildren(sidebar, report);
      const listHeader = el("div", "mast-list-header");
      listHeader.append(el("span", "section-title", "Reports"),
        tip(analyzeButton("New", "ghost"), `Analyze this branch up to event ${selection.cursor}`));
      sidebar.replaceChildren(listHeader, ...jobs.map((item) => reportListItem(item, item.id === job.id, host)));
      host.setViewParams("mast", { report: job.id });
      const signature = JSON.stringify(job);
      if (signature !== rendered) {
        const modes = await loadDefinitions();
        if (ticket !== generation) return;
        renderReport(report, job, host, modes);
        rendered = signature;
      }
      clearTimeout(timer);
      if (jobs.some((item) => active.has(item.status))) timer = setTimeout(() => refresh().catch(showError), 1500);
    }

    function showError(error) {
      if (ticket !== generation) return;
      const message = emptyState(error.message, button("Retry", () => refresh().catch(showError)));
      message.querySelector("p").className = "mast-error";
      if (params.report) message.append(button("All reports", () => host.openView("mast"), "ghost"));
      root.replaceChildren(message);
    }

    root.replaceChildren(emptyState("Loading reports…"));
    await refresh().catch(showError);
  }
}

export function installMast(manifest, host) {
  installReportView(manifest, host);
  host.addAction({ id: "mast-analyze", label: "Analyze with MAST…",
    onClick: () => analyzeDialog(manifest, host.selection(), host) });
}
