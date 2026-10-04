import { el, button, post, toast, failure, formatNumber, segmented } from "./ui.js";
import { details, isActive, longDate, openAnalysisDialog, progressRow, reportHeader, statTile } from "./reports.js";

// The timeline plugin's start dialog and saved reports: milestones in run order, misalignment flags first-class.

const methodNotes = {
  orchestrated: "Reads chunks of the run in parallel, then one call merges their findings into the timeline.",
  single: "One long-context call reads the whole run.",
  goal_tree: "Halves the run into sections, reads each one, and merges the halves back up.",
};
const windowNouns = { orchestrated: "chunk", goal_tree: "section" };

const humanize = (value) => value.charAt(0).toUpperCase() + value.slice(1).replaceAll("_", " ");
const plural = (count, noun) => `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;
const compact = (value) => value === null || value === undefined ? "—"
  : new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);

function duration(seconds) {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min ${Math.round(seconds % 60)} s` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const usageText = (usage) => `${plural(usage.calls, "model call")} · ${compact(usage.input_tokens)} input · ${compact(usage.output_tokens)} output tokens`;

// Start dialog

function previewSummary(preview) {
  const root = el("div", "tlr-preview");
  root.append(el("span", "", preview.ready ? `Ready · ${preview.model.model}` : preview.reason));
  if (!preview.events) return root;
  const stats = el("dl", "tlr-preview-stats");
  for (const [label, value] of [["Events", formatNumber(preview.events)],
    ["Estimated tokens", formatNumber(preview.estimated_tokens)],
    [humanize(`${windowNouns[preview.config.method] || "window"}s`), formatNumber(preview.windows)]]) {
    const item = el("div");
    item.append(el("dt", "", label), el("dd", "", value));
    stats.append(item);
  }
  root.append(stats);
  return root;
}

function analyzeDialog(manifest, prefix, selection, host) {
  if (!selection) return toast("Select a run to analyze");
  const { method: methodSchema, chunk_tokens: chunkSchema } = manifest.config.properties;
  let method = methodSchema.default;
  const methodNote = el("p", "muted tlr-method-note", methodNotes[method] || "");
  const chunkField = el("div", "tlr-chunk-field");
  const chunkLabel = el("label", "analysis-field-label", "Chunk size (tokens)");
  const chunkInput = el("input");
  Object.assign(chunkInput, { id: "timeline-chunk-tokens", type: "number", min: chunkSchema.minimum, step: 1,
    required: true, value: chunkSchema.default });
  chunkLabel.htmlFor = chunkInput.id;
  chunkField.append(chunkLabel, chunkInput);
  chunkField.hidden = method !== "orchestrated";

  const config = () => method === "orchestrated" ? { method, chunk_tokens: chunkInput.valueAsNumber } : { method };
  const body = () => ({ branch_id: selection.branchId, cursor: selection.cursor, config: config() });
  const fields = [el("span", "analysis-field-label", "Method"),
    segmented("Method", methodSchema.enum.map((value) => [value, humanize(value)]), method, (value) => {
      method = value;
      methodNote.textContent = methodNotes[value] || "";
      chunkField.hidden = value !== "orchestrated";
      refresh();
    }), methodNote, chunkField];
  const refresh = openAnalysisDialog({
    title: "Long-context LLM judge", fields,
    note: `Sends events 1–${selection.cursor} to ${manifest.model.model} once you click Analyze. The preview is free.`,
    check: async () => {
      if (selection.cursor < 1) return { ready: false, message: "Select at least one event first." };
      if (method === "orchestrated" && !(Number.isInteger(chunkInput.valueAsNumber) && chunkInput.valueAsNumber >= 1)) {
        return { ready: false, message: "Chunk size must be a whole number of tokens." };
      }
      const preview = await post(`${prefix}/preview`, body());
      return { ready: preview.ready, message: previewSummary(preview) };
    },
    start: async () => host.openReport(await post(`${prefix}/analyses`, body())),
  });
  let typing;
  chunkInput.addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(refresh, 300);
  });
}

// Report

function eventChips(positions, job, host) {
  const chips = el("span", "tlr-chips");
  chips.append(...positions.map((position) => {
    const chip = button(`#${position}`, () => host.showEvent(job.branch_id, position).catch(failure), "tlr-chip");
    chip.setAttribute("aria-label", `Open event ${position} in the explorer`);
    return chip;
  }));
  return chips;
}

function severityMeter(severity) {
  const meter = el("span", "tlr-severity");
  meter.setAttribute("role", "img");
  meter.setAttribute("aria-label", `Severity ${severity} of 3`);
  meter.title = `Severity ${severity} of 3`;
  for (let level = 1; level <= 3; level++) meter.append(el("i", level <= severity ? "on" : ""));
  return meter;
}

function milestoneItem(milestone, job, host) {
  const item = el("li", milestone.misaligned ? "tlr-milestone flagged" : "tlr-milestone");
  item.dataset.severity = milestone.severity;
  const node = el("span", "tlr-node");
  node.setAttribute("aria-hidden", "true");
  const card = el("article", "tlr-card");
  const heading = el("header", "tlr-card-header");
  heading.append(el("h4", "", milestone.title));
  if (milestone.misaligned) {
    const flag = el("span", "tlr-flag", "Misaligned");
    flag.append(severityMeter(milestone.severity));
    heading.append(flag);
  } else heading.append(el("span", "badge", humanize(milestone.kind)));
  card.append(heading, el("p", "tlr-text", milestone.description));
  const footer = el("footer", "tlr-card-footer");
  if (milestone.agents.length) {
    const agents = el("span", "tlr-agents");
    agents.append(...milestone.agents.map((agent) => el("span", "tlr-agent", agent)));
    footer.append(agents);
  }
  footer.append(eventChips(milestone.positions, job, host));
  card.append(footer);
  item.append(el("span", "tlr-position mono", `#${milestone.positions[0]}`), node, card);
  return item;
}

function milestonesSection(milestones, job, host) {
  const section = el("section", "tlr-section");
  const flags = milestones.filter((milestone) => milestone.misaligned).length;
  const heading = el("div", "tlr-section-header");
  const list = el("ol", "tlr-milestones");
  list.append(...milestones.map((milestone) => milestoneItem(milestone, job, host)));
  const noFlags = el("p", "muted tlr-none", "No misalignment flags in this report.");
  noFlags.hidden = true;
  heading.append(el("h3", "section-title", "Milestones"),
    segmented("Show milestones", [["all", `All · ${milestones.length}`], ["flags", `Flags only · ${flags}`]], "all", (value) => {
      const flagsOnly = value === "flags";
      for (const item of list.children) item.hidden = flagsOnly && !item.classList.contains("flagged");
      noFlags.hidden = !flagsOnly || flags > 0;
    }));
  section.append(heading);
  if (!milestones.length) section.append(el("p", "muted tlr-none", "No milestones were found in this history."));
  section.append(list, noFlags);
  return section;
}

function threadsSection(threads, job, host) {
  const section = el("section", "tlr-section");
  const heading = el("div", "tlr-section-header");
  heading.append(el("h3", "section-title", `Open threads · ${threads.length}`));
  section.append(heading, el("p", "muted", "Suspicions the analysis could not settle from the events it read."));
  if (!threads.length) {
    section.append(el("p", "muted tlr-none", "No open threads."));
    return section;
  }
  const list = el("ul", "tlr-threads");
  list.append(...threads.map((thread) => {
    const item = el("li", "tlr-thread");
    item.append(el("p", "tlr-text", thread.note), eventChips(thread.positions, job, host));
    return item;
  }));
  section.append(list);
  return section;
}

function stats(output) {
  const flags = output.milestones.filter((milestone) => milestone.misaligned).length;
  const strip = el("div", "report-stats");
  strip.append(
    statTile(formatNumber(flags), "Flags to review · reviewer workload", flags > 0),
    statTile(formatNumber(output.milestones.length), "Milestones in run order"),
    statTile(formatNumber(output.events_read), `Events read in ${plural(output.windows, windowNouns[output.method] || "window")}`),
    statTile(duration(output.seconds), usageText(output.usage)));
  return strip;
}

function runDetails(job, output) {
  return JSON.stringify({
    branch_id: job.branch_id, cursor: job.cursor, plugin_version: job.plugin_version, config: output.config,
    model: job.model, input_digest: job.analysis.input_digest, citations: output.citations, usage: output.usage,
    calls: output.calls.map(({ stage, status, seconds, input_tokens, output_tokens }) =>
      ({ stage, status, seconds, input_tokens, output_tokens })),
  }, null, 2);
}

function failureNotice(job) {
  const notice = el("div", "tlr-failure");
  notice.append(el("strong", "", job.error || "The analysis did not finish."));
  if (job.usage) notice.append(el("p", "", `Spent before stopping: ${usageText(job.usage)}.`));
  if (job.error_detail) {
    notice.append(details("Error detail", typeof job.error_detail === "string"
      ? job.error_detail : JSON.stringify(job.error_detail, null, 2)));
  }
  return notice;
}

function renderReport(root, job, host) {
  const subtitle = `${humanize(job.config.method)} · events 1–${job.cursor} · ${job.model.model} · ${longDate(job.created_at)}`;
  root.replaceChildren(reportHeader("Timeline", subtitle, job, host));
  if (isActive(job)) return root.append(progressRow(`Building a timeline of events 1–${job.cursor} with ${job.model.model}…`));
  if (!job.analysis) return root.append(failureNotice(job));
  const output = job.analysis.output;
  root.append(stats(output), milestonesSection(output.milestones, job, host),
    threadsSection(output.open_threads, job, host),
    el("p", "muted report-note", "LLM assessment, not human reviewed."),
    details("Run details", runDetails(job, output)));
}

export function installTimeline(manifest, host) {
  const prefix = manifest.api_prefix.replace(/^\/api/, "");
  const analyze = () => analyzeDialog(manifest, prefix, host.selection(), host);
  host.addReports({ title: "Timeline", prefix, render: (root, job) => renderReport(root, job, host), analyze });
  host.addAction({ id: "timeline-analyze", label: "Long-context LLM judge…", onClick: analyze });
}
