import { el, formatNumber, time } from "./ui.js";
import { field, openDialog } from "./dialog.js";
import { renderParamsForm } from "./params-form.js";

export const JOB_STATUS = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  needs_review: "Needs review",
  failed: "Failed",
  interrupted: "Interrupted",
};

function checked(form) {
  const problems = form.validate();
  if (problems.length) throw new Error(problems.join(" "));
  return form.values();
}

function positionField(label, name, value, head) {
  const control = field(label, name, String(value));
  Object.assign(control.input, { type: "number", min: 1, max: head, step: 1, required: true });
  const node = el("div");
  node.append(control.fragment);
  return { node, input: control.input };
}

// Runs an analyzer over events start..end of a branch; `submit({ start, end, params })` starts the job.
export function analysisDialog(plugin, { branchName, cursor, head }, submit) {
  const form = renderParamsForm(plugin.params_schema);
  const start = positionField("First event", "range_start", 1, head);
  const end = positionField("Last event", "range_end", Math.max(1, cursor), head);
  const range = el("div", "pf-range");
  range.append(start.node, end.node);
  const content = el("div", "plugin-dialog");
  if (plugin.description) content.append(el("p", "muted", plugin.description));
  content.append(range, form.element);
  openDialog(plugin.title, content, {
    kicker: `Analyze · ${branchName}`, confirm: "Run", pending: "Starting…",
    submit: async () => {
      const [first, last] = [Number(start.input.value), Number(end.input.value)];
      if (first > last) throw new Error("The first event must come before the last event.");
      await submit({ start: first, end: last, params: checked(form) });
    },
  });
}

// Forks at `position` through an intervention plugin; `submit({ name, params })` creates the branch.
export function interventionDialog(plugin, { branchName, position }, submit) {
  const form = renderParamsForm(plugin.params_schema);
  const name = field("Branch name", "branch_name", `${plugin.title} at ${position}`);
  Object.assign(name.input, { required: true, maxLength: 160 });
  const content = el("div", "plugin-dialog");
  content.append(el("p", "fork-effect", `New branch after event ${formatNumber(position)}.${plugin.description ? ` ${plugin.description}` : ""}`),
    name.fragment, form.element);
  openDialog(`Fork with ${plugin.title}`, content, {
    kicker: `From ${branchName}`, confirm: "Create branch", pending: "Creating branch…",
    submit: () => submit({ name: name.input.value, params: checked(form) }),
  });
}

export function jobDialog(job, title) {
  const content = el("div", "plugin-dialog");
  content.append(el("p", "", `${JOB_STATUS[job.status]} · events ${formatNumber(job.start)}–${formatNumber(job.end)} · started ${time(job.created_at)} UTC`));
  if (job.error) content.append(el("p", "ins-error content-text", job.error));
  content.append(el("pre", "metadata-box", JSON.stringify(job.config ?? {}, null, 2)));
  openDialog(title, content, { kicker: `${job.plugin_id} v${job.plugin_version}` });
}
