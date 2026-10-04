// Stance lanes view: shows the newest finished stance-lanes analysis of the open branch.
import { el, api, post, failure, toast } from "swarm-lens/ui.js";
import { field, openDialog } from "swarm-lens/dialog.js";
import { renderLanes } from "./lanes.js";

const PLUGIN = "stance-lanes";

function addStyles() {
  if (document.querySelector("link[data-stance-lanes]")) return;
  const link = el("link");
  link.rel = "stylesheet";
  link.href = new URL("./lanes.css", import.meta.url).href;
  link.dataset.stanceLanes = "";
  document.head.append(link);
}

// The newest finished analysis this branch can see: its own, else an ancestor's that ends at or before the
// fork (the same rule as the generic findings).
export async function latestReport(branchId, branches = []) {
  const byId = new Map(branches.map((branch) => [branch.id, branch]));
  for (let id = branchId, limit = Infinity; id; ) {
    const { jobs } = await api(`/branches/${encodeURIComponent(id)}/analyses?plugin_id=${PLUGIN}`);
    const job = jobs.find((item) => item.status === "completed" && item.analysis?.output?.report
      && item.analysis.end <= limit);
    if (job) return { job, report: job.analysis.output.report };
    const branch = byId.get(id);
    limit = Math.min(limit, branch?.fork_position ?? 0);
    id = branch?.parent_id;
  }
  return null;
}

export function flipActions(host, branchId) {
  return {
    open: (item) => host.openTimeline(branchId, item.seq, item.event_id).catch(failure),
    openRead: (flip, peer) => host.openTimeline(branchId, flip.read[peer].seq, flip.read[peer].event_id).catch(failure),
    fork: (flip) => {
      const cursor = flip.seq - 1;
      const { fragment, input } = field("Branch name", "name", `Before ${flip.agent_id} flips to ${flip.to}`);
      input.required = true;
      const content = el("div");
      content.append(el("p", "muted", `The new branch keeps events 1 to ${cursor}, so ${flip.agent_id}'s flip at `
        + `event ${flip.seq} has not happened yet. The original branch never changes.`), fragment);
      openDialog("Fork before flip", content, {
        confirm: "Fork", pending: "Forking…",
        submit: async (form) => {
          const branch = await post(`/branches/${encodeURIComponent(branchId)}/fork`, { cursor, name: form.get("name") });
          toast(`Forked at event ${cursor}`);
          await host.openTimeline(branch.id, cursor);
        },
      });
    },
  };
}

export function install(host) {
  addStyles();
  let generation = 0;
  const panel = host.registerView({
    id: PLUGIN, title: "Stance lanes",
    tip: "Each agent's stance per message, its flips and whom it flipped toward",
    onShow: () => show(++generation),
  });
  panel.classList.add("stance-view");
  const message = (text) => el("p", "stance-empty muted", text);

  // Only the latest show() may fill the panel, so a slow load for a branch left behind cannot replace it.
  async function show(current) {
    const { branch, workspace } = host.context();
    if (!branch) return panel.replaceChildren(message("Open a run to see its stance lanes."));
    panel.replaceChildren(message("Loading…"));
    let content;
    try {
      const found = await latestReport(branch.id, workspace?.branches);
      content = found ? renderLanes(found.report, flipActions(host, branch.id))
        : message("No stance-lanes analysis on this branch yet. Run Stance lanes from the Plugins menu, with a "
          + "regex pattern or a stance question.");
    } catch (error) {
      content = message(error.message);
    }
    if (current === generation) panel.replaceChildren(content);
  }

  host.addAction({ label: "Open stance lanes", tip: "Show the stance lanes of this branch", onClick: () => host.openView(PLUGIN) });
}
