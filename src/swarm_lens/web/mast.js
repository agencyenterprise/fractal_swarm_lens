import { el, post, toast, formatNumber, segmented } from "./ui.js";
import { mastTraitDetails } from "./mast-details.js";
import { details, isActive, longDate, openAnalysisDialog, progressRow, reportHeader, statTile } from "./reports.js";

const prefix = "/plugins/mast";
const completenessOptions = [["unknown", "Unknown"], ["complete", "Complete"], ["partial", "Partial"]];

function header(job, host) {
  return reportHeader("MAST report", `events 1–${job.cursor} · ${job.judge.model} · ${longDate(job.created_at)}`, job, host);
}

function outcomeStrip(output) {
  const present = output.labels.filter((label) => label.present === true).length;
  const completed = output.task_completed === null ? "Unparsed" : output.task_completed ? "Yes" : "No";
  const strip = el("div", "report-stats");
  strip.append(statTile(completed, "Task completed · judge assessment", output.task_completed === false),
    statTile(String(present), `of ${output.labels.length} failure modes present`, present > 0));
  return strip;
}

// Every row expands into the saved explanation and evidence for that trait.
function modeList(labels, job, host) {
  const list = el("div", "mast-modes");
  list.append(...labels.map((label) => mastTraitDetails(label, job, host)));
  return list;
}

function failureModes(labels, job, host) {
  const section = el("section", "mast-failures");
  const present = labels.filter((label) => label.present === true);
  const unparsed = labels.filter((label) => label.present === null);
  const absent = labels.filter((label) => label.present === false);
  const groups = [...new Set(present.map((label) => label.group))];
  for (const group of groups) {
    section.append(el("h3", "section-title", group),
      modeList(present.filter((label) => label.group === group), job, host));
  }
  if (unparsed.length) {
    section.append(el("h3", "section-title", "Unparsed"), modeList(unparsed, job, host));
  }
  if (absent.length) section.append(details(`${absent.length} not present`, modeList(absent, job, host), "mast-absent"));
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

function renderReport(root, job, host) {
  root.replaceChildren(header(job, host));
  if (job.error) root.append(el("p", "mast-error", job.error));
  if (isActive(job)) return root.append(progressRow(`Analyzing events 1–${job.cursor} with ${job.judge.model}…`));
  if (!job.analysis) return root.append(el("p", "muted", "No assessment was produced."));
  const output = job.analysis.output;
  root.append(outcomeStrip(output));
  if (output.summary) root.append(el("p", "mast-summary", output.summary));
  root.append(failureModes(output.labels, job, host), el("p", "muted report-note", "LLM assessment, not human reviewed."));
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

function sizeCheckText(preview) {
  if (!preview.can_analyze) return preview.reason || "This snapshot cannot be analyzed.";
  if (preview.estimated_input_tokens === undefined) return `Fits · ${formatNumber(preview.event_count)} events`;
  return `Fits · ${formatNumber(preview.estimated_input_tokens)} of ${formatNumber(preview.input_token_limit)} tokens`;
}

function analyzeDialog(manifest, selection, host) {
  if (!selection) return toast("Select a run to analyze");
  let completeness = "unknown";
  const body = () => ({ branch_id: selection.branchId, cursor: selection.cursor, completeness });
  const fields = [el("span", "analysis-field-label", "Trace completeness"),
    segmented("Trace completeness", completenessOptions, completeness, (value) => {
      completeness = value;
      refresh();
    })];
  if (!manifest.judge.ready) fields.push(el("p", "mast-error", manifest.judge.reason));
  const refresh = openAnalysisDialog({
    title: "Analyze with MAST", fields,
    note: `Sends events 1–${selection.cursor} to OpenAI ${manifest.judge.model}, then one more request to locate evidence for each trait.`,
    check: async () => {
      if (selection.cursor < 1) return { ready: false, message: "Select at least one event first." };
      const preview = await post(`${prefix}/preview`, body());
      return { ready: preview.can_analyze, message: sizeCheckText(preview) };
    },
    start: async () => host.openReport(await post(`${prefix}/analyses`, body())),
  });
}

export function installMast(manifest, host) {
  const analyze = () => analyzeDialog(manifest, host.selection(), host);
  host.addReports({ title: "MAST", prefix, render: (root, job) => renderReport(root, job, host), analyze });
  host.addAction({ id: "mast-analyze", label: "Analyze with MAST…", onClick: analyze });
}
