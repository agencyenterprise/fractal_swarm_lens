import { $, el, button, api, post, toast, failure } from "./ui.js";
import { field, openDialog } from "./dialog.js?v=4";
import { workspaceURL } from "./workspace.js";
import { mastTraitDetails } from "./mast-details.js?v=2";

const prefix = "/plugins/mast";
const active = new Set(["queued", "running"]);
const statusLabel = (value) => ({ needs_review: "Needs review", completed: "Completed",
  queued: "Queued", running: "Analyzing", failed: "Failed", interrupted: "Interrupted" }[value] || value);

function details(title, text) {
  const node = el("details", "mast-details");
  node.append(el("summary", "", title), el("pre", "metadata-box", text));
  return node;
}

function reportURL(job) {
  return workspaceURL({ branchId: job.branch_id, cursor: job.cursor, view: "mast", report: job.id });
}

function reportLink(job, label, onOpen) {
  const link = el("a", "mast-report-link", label);
  link.href = reportURL(job);
  link.onclick = (event) => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen(job);
  };
  return link;
}

function downloadResult(job) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(job, null, 2)], { type: "application/json" }));
  const link = el("a");
  link.href = url;
  link.download = `mast-${job.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderResult(root, job, host) {
  root.replaceChildren();
  const heading = el("div", "mast-result-heading");
  const title = el("div");
  title.append(el("span", "eyebrow", "SAVED ASSESSMENT"), el("h3", "", "MAST analysis report"),
    el("p", "muted", `${new Date(job.created_at).toLocaleString()} · ${job.judge.model}`));
  heading.append(title, el("span", `mast-badge ${job.status === "needs_review" ? "unknown" : ""}`, statusLabel(job.status)));
  const scope = el("div", "mast-report-scope");
  scope.append(el("p", "", `Analyzed events 1–${job.cursor} · ${job.config.completeness} trace`),
    button("View analyzed snapshot →", () => host.showSnapshot(job.branch_id, job.cursor).catch(failure), "subtle"));
  if (job.input_summary) {
    const input = job.input_summary;
    scope.append(el("p", "muted", `${input.event_count} events retained · ${input.message_count} messages · ${input.memory_snapshot_count} memory snapshots · repeated text stored once`));
  }
  const actions = el("div", "mast-report-actions");
  const permalink = el("a", "mast-permalink", "Report link ↗");
  permalink.href = reportURL(job);
  permalink.target = "_blank";
  permalink.rel = "noopener";
  const copy = button("Copy report link", async () => {
    try { await navigator.clipboard.writeText(reportURL(job)); toast("Report link copied."); }
    catch { toast("Use the Report link to open or copy this report's address."); }
  });
  actions.append(permalink, copy, button("Download JSON", () => downloadResult(job)));
  root.append(heading, scope, actions);
  if (job.error) root.append(el("p", "mast-error", job.error));
  if (active.has(job.status)) {
    const progress = el("div", "mast-empty");
    progress.setAttribute("role", "status");
    progress.append(el("h3", "", "Analyzing the saved conversation…"),
      el("p", "", "You can return to the timeline or reopen this report later. The analysis continues on the server."));
    root.append(progress);
  } else if (!job.analysis) {
    root.append(el("p", "mast-empty", "No assessment was produced. Start a new analysis to try again."));
  }
  if (!job.analysis) return;
  const output = job.analysis.output;
  const present = output.labels.filter((label) => label.present === true).length;
  const unknown = output.labels.filter((label) => label.present === null).length;
  const completion = output.task_completed === null ? "Unparsed" : output.task_completed ? "Yes" : "No";
  const stats = el("div", "mast-stats");
  for (const [value, label] of [[`${present} / ${output.labels.length}`, "Failure modes present"],
    [completion, "Task completed · judge assessment"], [String(unknown), "Unparsed categories"]]) {
    const stat = el("div", "mast-stat");
    stat.append(el("strong", "", value), el("span", "", label));
    stats.append(stat);
  }
  const summary = el("section", "mast-summary-section");
  summary.append(el("h4", "", "Assessment summary"),
    el("p", "mast-summary", output.summary || "The judge did not provide a summary."));
  root.append(stats, summary);
  const categories = el("div", "mast-categories");
  for (const [prefix, title] of [["1.", "Specification & system design"], ["2.", "Inter-agent misalignment"], ["3.", "Task verification & termination"]]) {
    const group = el("section", "mast-category-group");
    group.append(el("h4", "", title));
    for (const label of output.labels.filter((item) => item.code.startsWith(prefix))) {
      group.append(mastTraitDetails(label, job, host));
    }
    categories.append(group);
  }
  root.append(categories, el("p", "mast-assessment-note", "LLM assessment · not human reviewed. MAST classifies failure modes; these labels do not establish cascade occurrence."));
  if (output.warnings.length) root.append(details("Parsing notes", output.warnings.join("\n")));
  if (output.evidence?.warnings?.length) root.append(details("Evidence notes", output.evidence.warnings.join("\n")));
  if (output.evidence) root.append(details("Evidence provenance", JSON.stringify({
    version: output.evidence.version, status: output.evidence.status,
    prompt_sha256: output.evidence.prompt_sha256, judge: output.evidence.judge,
  }, null, 2)));
  root.append(details("Raw judge response", output.raw_response),
    details("Upstream taxonomy notes", output.upstream.upstream_notes.join("\n\n")),
    details("Analysis provenance", JSON.stringify({
      branch_id: job.branch_id, cursor: job.cursor, completeness: job.config.completeness,
      input_digest: job.input_digest, plugin_version: job.plugin_version,
      upstream_revision: output.upstream.revision, prompt_sha256: output.prompt_sha256,
      trace_sha256: output.trace_sha256, judge: output.judge,
      input_summary: job.input_summary,
    }, null, 2)));
}

function analyzeDialog(manifest, selection, host) {
  if (!selection) return;
  const root = el("div", "mast-panel");
  root.append(el("p", "", `${selection.name} · ${selection.branchName} · saved snapshot through event ${selection.cursor}.`));
  const completeness = field("Trace completeness", "completeness", "", "select");
  completeness.input.setOptions([
    { value: 'unknown', label: 'Unknown', description: 'Completeness has not been established.' },
    { value: 'complete', label: 'Complete task trace', description: 'The entire task is included in this snapshot.' },
    { value: 'partial', label: 'Partial trace', description: 'This snapshot contains part of the task.' },
  ]);
  root.append(completeness.fragment,
    el("p", "", `The selected trace will be sent to OpenAI (${manifest.judge.model}) with MAST's definitions and examples.`),
    el("p", "", "A second model request will locate supporting messages and context for the trait details."),
    el("p", "", "Your results will open as a saved report in the workspace."));
  const inputStatus = el("div", "mast-input-preview");
  inputStatus.setAttribute("role", "status");
  root.append(inputStatus);
  if (!manifest.judge.ready) root.append(el("p", "mast-error", manifest.judge.reason));
  if (selection.cursor < 1) root.append(el("p", "mast-error", "Select at least one recorded event before analyzing."));
  openDialog("Start MAST analysis", root, { kicker: "SAVED TRACE ANALYSIS", confirm: "Analyze saved trace",
    submit: async () => {
      if (!ready) throw new Error("Wait for the complete-trace size check before starting analysis.");
      const job = await post(`${prefix}/analyses`, { branch_id: selection.branchId,
        cursor: selection.cursor, completeness: completeness.input.value });
      await host.openReport(job);
    },
  });
  let ready = false;
  let generation = 0;
  async function checkInput() {
    const ticket = ++generation;
    ready = false;
    $("#confirm-dialog").disabled = true;
    inputStatus.replaceChildren(el("p", "muted", "Checking the complete saved trace…"));
    try {
      const preview = await post(`${prefix}/preview`, { branch_id: selection.branchId,
        cursor: selection.cursor, completeness: completeness.input.value });
      if (ticket !== generation || !root.isConnected || !$("#dialog").open) return;
      ready = preview.can_analyze;
      const count = (value) => Number(value).toLocaleString();
      inputStatus.replaceChildren(
        el("strong", "", ready ? "Complete saved snapshot fits" : "This saved snapshot needs attention"),
        el("p", "", `${count(preview.event_count)} events · ${count(preview.message_count)} messages · ${count(preview.memory_snapshot_count)} memory snapshots retained.`),
        el("p", "muted", `Repeated text is referenced once. No turns are removed or summarized.`));
      if (preview.estimated_input_tokens !== undefined) {
        inputStatus.append(el("p", "muted", `About ${count(preview.estimated_input_tokens)} input tokens, including MAST's instructions and examples, of ${count(preview.input_token_limit)} available. Output space is reserved.`));
      }
      if (preview.reason) inputStatus.append(el("p", "mast-error", preview.reason));
    } catch (error) {
      if (ticket !== generation || !root.isConnected || !$("#dialog").open) return;
      inputStatus.replaceChildren(el("p", "mast-error", error.message));
    }
    if (ticket === generation && root.isConnected && $("#dialog").open) $("#confirm-dialog").disabled = !ready;
  }
  completeness.input.onchange = checkInput;
  if (selection.cursor >= 1) checkInput();
  else $("#confirm-dialog").disabled = true;
}

function installReportView(manifest, host) {
  let timer;
  let generation = 0;
  const stop = () => { generation++; clearTimeout(timer); };
  const root = host.registerView({ id: "mast", title: "MAST reports", onShow: show, onHide: stop });
  async function show(params) {
    const ticket = ++generation;
    const selection = host.selection();
    root.replaceChildren();
    const header = el("header", "mast-workspace-header");
    const title = el("div");
    title.append(el("span", "eyebrow", "CONVERSATION ANALYSIS"), el("h2", "", "MAST reports"),
      el("p", "muted", selection ? `${selection.name} · ${selection.branchName}` : "Choose or import a conversation to begin."));
    const actions = el("div", "mast-report-actions");
    const create = button("New analysis", () => analyzeDialog(manifest, host.selection(), host), "primary");
    create.disabled = !selection;
    actions.append(button("← Timeline", () => host.openView("timeline")), create);
    header.append(title, actions);
    root.append(header);
    if (!selection) return;
    const layout = el("div", "mast-report-layout");
    const sidebar = el("nav", "mast-report-list");
    sidebar.setAttribute("aria-label", "Saved MAST reports");
    const result = el("article", "mast-result");
    result.setAttribute("aria-label", "MAST analysis report");
    result.append(el("p", "mast-empty", "Loading saved reports…"));
    layout.append(sidebar, result);
    root.append(layout);
    let selected = params.report;
    let rendered = "";
    async function refresh() {
      const data = await api(`${prefix}/analyses?branch_id=${encodeURIComponent(selection.branchId)}`);
      if (ticket !== generation) return;
      const jobs = data.jobs;
      selected ||= jobs[0]?.id;
      let job = jobs.find((item) => item.id === selected);
      // Direct links remain usable after a report falls outside the latest 50 jobs.
      if (!job && selected) job = await api(`${prefix}/analyses/${encodeURIComponent(selected)}`);
      if (ticket !== generation) return;
      if (job && job.branch_id !== selection.branchId) throw new Error("This report belongs to a different conversation branch.");
      if (job && !jobs.some((item) => item.id === job.id)) jobs.push(job);
      sidebar.replaceChildren(el("h3", "eyebrow", "SAVED REPORTS"));
      for (const item of jobs) {
        const link = reportLink(item, "", (chosen) => host.openView("mast", { report: chosen.id }));
        link.classList.toggle("active", item.id === selected);
        if (item.id === selected) link.setAttribute("aria-current", "page");
        link.append(el("strong", "", `Events 1–${item.cursor}`),
          el("span", "", new Date(item.created_at).toLocaleString()), el("span", "mast-report-status", statusLabel(item.status)));
        sidebar.append(link);
      }
      if (job) {
        host.setViewParams("mast", { report: job.id });
        const signature = JSON.stringify(job);
        if (signature !== rendered) { renderResult(result, job, host); rendered = signature; }
      } else {
        sidebar.append(el("p", "muted", "No reports yet"));
        result.replaceChildren(el("h3", "", "This conversation has no MAST reports yet."),
          el("p", "mast-empty", "Start a new analysis of the selected timeline snapshot. Its assessment will be saved here."));
      }
      clearTimeout(timer);
      if (jobs.some((item) => active.has(item.status))) timer = setTimeout(() => refresh().catch(showError), 1500);
    }
    function showError(error) {
      if (ticket !== generation) return;
      result.replaceChildren(el("h3", "", "Unable to load this report"), el("p", "mast-error", error.message),
        button("Retry", () => refresh().catch(showError)),
        button("Show branch reports", () => host.openView("mast"), "subtle"));
    }
    await refresh().catch(showError);
  }
}

function importDialog(onImport) {
  const root = el("div");
  const name = field("Trace name", "name", "");
  name.input.required = true;
  name.input.maxLength = 160;
  const task = field("Task or goal (optional)", "task", "");
  const trace = field("Saved transcript", "trace", "", "textarea");
  trace.input.required = true;
  trace.input.maxLength = 200000;
  trace.input.placeholder = "Paste the recorded conversation, including agent names and task instructions when available.";
  const file = field("Or choose a UTF-8 trace file (.txt, .json, .jsonl)", "file", "");
  file.input.type = "file";
  file.input.accept = ".txt,.json,.jsonl";
  file.input.onchange = async () => {
    try {
      const chosen = file.input.files[0];
      if (!chosen) return;
      if (chosen.size > 800000) throw new Error("Choose a trace file smaller than 800 KB.");
      const text = await chosen.text();
      if (text.length > 200000) throw new Error("Choose a trace shorter than 200,000 characters.");
      trace.input.value = text;
      if (!name.input.value) name.input.value = chosen.name.slice(0, 160);
      $("#dialog-error").textContent = "";
    } catch (error) { $("#dialog-error").textContent = error.message; }
  };
  root.append(el("p", "", "The original text is retained locally. Importing does not call an LLM. Structured agent lanes require a source adapter; this importer preserves a raw transcript."),
    name.fragment, task.fragment, file.fragment, trace.fragment);
  openDialog("Import a saved trace", root, { kicker: "TRACE COLLECTION", confirm: "Import trace",
    submit: async () => {
      const result = await post(`${prefix}/traces`, { name: name.input.value,
        task: task.input.value, text: trace.input.value });
      await onImport(result.branch.id);
      toast("Trace saved. Open MAST analysis when you are ready to assess it.");
    },
  });
}

export function installMast(manifest, host) {
  installReportView(manifest, host);
  $("#mast-analyze").hidden = false;
  $("#mast-analyze").disabled = !host.selection();
  $("#mast-analyze").onclick = () => analyzeDialog(manifest, host.selection(), host);
  $("#import-trace").hidden = false;
  $("#import-trace").onclick = () => importDialog(host.onImport);
}
