import {
  $,
  $$,
  el,
  button,
  api,
  post,
  toast,
  failure,
  time,
  date,
  formatNumber,
  color,
  logoFor,
} from "./ui.js";
import { renderGraph } from "./graph.js";
import { EventTimeline } from "./timeline.js?v=4";
import { field, openDialog } from "./dialog.js?v=4";
import { branchDialog } from "./branch.js?v=3";
import { Picker } from './components.js?v=1';
import { installMast } from "./mast.js?v=6";
import { WorkspaceViews, workspaceURL, readWorkspaceRoute } from "./workspace.js?v=2";
import { LiveView } from "./live.js?v=6";
import {
  inspectAgent,
  inspectEnvironment,
  inspectEvent,
  inspectAnalysis,
} from "./inspect.js?v=2";

const view = {
  workspace: null,
  run: null,
  branch: null,
  events: [],
  state: null,
  cursor: 0,
  agent: null,
  event: null,
  filter: "messages",
  query: "",
  limit: 45,
  playing: false,
};
let requestId = 0,
  seekTimer,
  playTimer;
const runPicker = new Picker({ id: 'conversation', label: 'Search conversations', searchable: true,
  dark: true, hideLabel: true, placeholder: 'Search conversations…' });
$('#run-select').append(runPicker.root);
runPicker.mount();
const speedPicker = new Picker({ id: 'playback-step', label: 'Playback step size', compact: true, hideLabel: true,
  value: '10', options: [1, 10, 50].map(value => ({ value, label: `${value} event${value === 1 ? '' : 's'}` })) });
$('#speed').append(speedPicker.root);
speedPicker.mount();
const actions = {
  selectAgent,
  editAgent,
  removeAgent,
  readMemory,
  readArtifact,
  editGoal,
};
const timeline = new EventTimeline({
  onSeek: (position) => {
    liveView.pause();
    stopPlayback();
    clearTimeout(seekTimer);
    seekTimer = setTimeout(() => seek(position), 45);
  },
  onAgent: selectAgent,
  onEvent: timelineEvent,
  onPoint: async (position, point) => {
    liveView.pause(); stopPlayback(); clearTimeout(seekTimer);
    const branch = view.branch.id;
    try {
      await seek(position);
      if (view.branch.id === branch && view.cursor === position) showPointMenu(point);
    } catch (error) { failure(error); }
  },
});
const installedPlugins = new Set();
const workspaceViews = new WorkspaceViews((id) => {
  openWorkspaceView(id, workspaceViews.entries.get(id).params);
});
workspaceViews.register({ id: "timeline", title: "Timeline", panel: $("#workspace-timeline") });
const liveView = new LiveView({
  selection: () => view.branch && ({ branchId: view.branch.id, cursor: view.cursor,
    head: view.branch.head, parentId: view.branch.parent_id, branchName: view.branch.name, runName: view.run.name }),
  seek,
  forked: async id => { await refreshWorkspace(); updateRunPicker(); await loadBranch(id); },
  update: async (data, follow) => {
    if (data.branch.id !== view.branch?.id) return;
    const events = data.events.filter(event => event.position > view.events.length);
    if (!events.length) return;
    if (events[0].position !== view.events.length + 1) throw new Error('Live event gap; reconnecting to recover history.');
    view.events.push(...events);
    view.branch = { ...data.branch, head: data.cursor };
    const stored = view.workspace.branches.find(b => b.id === view.branch.id);
    if (stored) Object.assign(stored, view.branch);
    $("#cursor").max = view.branch.head;
    timeline.setData(view.events, view.branch);
    renderBranches();
    await seek(follow ? view.branch.head : view.cursor);
  },
});

function selection() {
  return view.branch ? { branchId: view.branch.id, cursor: view.cursor,
    name: view.run.name, branchName: view.branch.name } : null;
}

function saveWorkspaceRoute(replace = true) {
  if (!view.branch) return;
  const url = workspaceURL({ ...selection(), view: workspaceViews.current || "timeline", ...workspaceViews.params });
  if (url !== location.href) history[replace ? "replaceState" : "pushState"](null, "", url);
}

function openWorkspaceView(id, params = {}, { replace = false, write = true } = {}) {
  stopPlayback();
  clearTimeout(seekTimer);
  $("#event-menu").hidden = true;
  workspaceViews.show(id, params);
  if (write) saveWorkspaceRoute(replace);
}

async function restoreWorkspaceRoute() {
  const route = readWorkspaceRoute();
  if (route.branchId && route.branchId !== view.branch?.id) {
    await loadBranch(route.branchId, route.cursor);
  } else if (route.cursor !== undefined && view.branch && route.cursor !== view.cursor) {
    await seek(route.cursor);
  }
  openWorkspaceView(route.view, { report: route.report }, { replace: true });
}
window.addEventListener("hashchange", () => restoreWorkspaceRoute().catch(failure));

async function refreshWorkspace() {
  view.workspace = await api("/workspace");
  const rank = (run) => run.metadata.source_type === "aciarena_example"
    ? (run.metadata.condition === "control" ? 0 : 1) : 2;
  view.workspace.runs.sort((a, b) => rank(a) - rank(b));
}

function updateRunPicker() {
  runPicker.setOptions(view.workspace.runs.map(run => {
    const branches = view.workspace.branches.filter(branch => branch.run_id === run.id);
    const parts = run.name.split(' · ');
    return { value: run.id, label: parts[0],
      badge: run.metadata.framework?.toUpperCase() || (run.metadata.source_type === 'aciarena_example' ? 'ACIARENA' : 'TRACE'),
      description: [parts.slice(1).join(' · '), `${time(run.created_at)} UTC`].filter(Boolean).join(' · '),
      detail: `${date(run.created_at)} · ${branches.length} branch${branches.length === 1 ? '' : 'es'} · ${run.id.slice(0, 6)}` };
  }));
  if (view.run) runPicker.value = view.run.id;
}

function renderBranches() {
  const root = $("#branches");
  root.replaceChildren();
  const branches = view.workspace.branches.filter(
    (branch) => branch.run_id === view.run.id,
  );
  const depth = (branch) => {
    let count = 0;
    while (branch.parent_id) {
      count++;
      branch = branches.find((parent) => parent.id === branch.parent_id);
      if (!branch) break;
    }
    return count;
  };
  for (const branch of branches) {
    const node = button(
      "",
      () => loadBranch(branch.id).catch(failure),
      "branch" + (branch.id === view.branch.id ? " active" : ""),
    );
    node.style.paddingLeft = 9 + Math.min(depth(branch), 4) * 10 + "px";
    node.setAttribute(
      "aria-current",
      branch.id === view.branch.id ? "true" : "false",
    );
    node.append(
      el("span", "branch-name", (branch.parent_id ? "⑂ " : "◉ ") + branch.name),
      el(
        "span",
        "",
        branch.parent_id
          ? `Forked at ${formatNumber(branch.fork_position)} · ${formatNumber(branch.head - branch.fork_position)} new`
          : `${formatNumber(branch.head)} recorded events`,
      ),
    );
    root.append(node);
  }
  $("#compare").disabled = branches.length < 2;
}

async function loadBranch(id, cursor) {
  stopPlayback();
  $("#footer-status").textContent = "Loading branch history…";
  const data = await api(`/branches/${id}/timeline`);
  workspaceViews.reset();
  openWorkspaceView("timeline", {}, { write: false });
  view.branch = data.branch;
  view.events = data.events;
  view.run = view.workspace.runs.find((run) => run.id === view.branch.run_id);
  if (view.run.metadata.source_type === "saved_trace" && view.filter === "messages") {
    view.filter = "all";
    $$("[data-filter]").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.filter === "all");
      tab.setAttribute("aria-selected", String(tab.dataset.filter === "all"));
    });
  }
  view.agent = null;
  view.event = null;
  view.limit = 45;
  runPicker.value = view.run.id;
  $("#branch-name").textContent = view.branch.name;
  $("#branch-status").textContent = view.branch.parent_id
    ? "Forked trajectory"
    : "Observed history";
  $("#context-title").textContent = view.branch.parent_id
    ? "Branch state"
    : view.run.metadata.source_type === "aciarena_example"
      ? `ACIArena · ${view.run.metadata.condition_label} · ${view.run.metadata.max_turn} rounds`
      : "Recorded trajectory";
  const pairedRun = view.run.metadata.example_pair && view.workspace.runs.find((run) =>
    run.id !== view.run.id && run.metadata.example_pair === view.run.metadata.example_pair);
  const pairedBranch = pairedRun && view.workspace.branches.find((branch) => branch.run_id === pairedRun.id && !branch.parent_id);
  const pairedLink = $("#paired-example");
  pairedLink.hidden = !pairedBranch;
  if (pairedBranch) {
    pairedLink.textContent = `${pairedRun.metadata.condition_label} →`;
    pairedLink.href = workspaceURL({ branchId: pairedBranch.id, cursor: pairedBranch.head });
    pairedLink.onclick = (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      loadBranch(pairedBranch.id).catch(failure);
    };
  }
  $("#dataset-link").href = view.run.metadata.dataset_url || "#";
  $("#dataset-link").hidden = !view.run.metadata.dataset_url;
  $("#attribution").textContent =
    view.run.metadata.attribution ||
    view.run.metadata.repository ||
    (view.run.metadata.framework && `${view.run.metadata.framework} ${view.run.metadata.framework_version || ""}`) ||
    "Source application";
  $("#source-date").textContent = date(view.run.metadata.start || view.run.created_at) + " · UTC";
  $("#cursor").max = view.branch.head;
  renderBranches();
  timeline.setData(view.events, view.branch);
  await seek(cursor ?? view.branch.head);
  liveView.connect(view.branch, view.run.metadata.source_type === "live" && cursor === undefined);
  if (!$("#mast-analyze").hidden) $("#mast-analyze").disabled = false;
}

async function seek(cursor) {
  cursor = Math.max(0, Math.min(view.branch.head, Number(cursor)));
  view.cursor = cursor;
  saveWorkspaceRoute();
  $("#cursor").value = cursor;
  $("#position-label").textContent =
    `${formatNumber(cursor)} / ${formatNumber(view.branch.head)}`;
  timeline.setCursor(cursor, true);
  const ticket = ++requestId;
  try {
    const state = await api(
      `/branches/${view.branch.id}/state?cursor=${cursor}`,
    );
    if (ticket !== requestId) return;
    view.state = state;
    if (view.event && view.event.position > cursor) view.event = null;
    if (view.agent && !state.agents[view.agent]) view.agent = null;
    $("#task-name").textContent = view.run.metadata.task_title || state.environment.task || view.run.name;
    $("#task-description").textContent =
      view.run.metadata.source_type === "aciarena_example"
        ? `${view.run.metadata.condition_label} · ${view.run.metadata.max_turn} debate rounds · 3 debaters + aggregator`
        : state.environment.goal || "Explore recorded swarm state.";
    $("#read-task").hidden = view.run.metadata.source_type !== "aciarena_example";
    $("#cursor-time").textContent =
      `${date(state.occurred_at)} · ${time(state.occurred_at)} UTC`;
    $("#counts").replaceChildren(
      ...[
        ["agents", "agents"],
        ["messages", "messages"],
        ["tools", "tool calls"],
        ["memories", "memory slots"],
      ].map(([key, label]) => {
        const node = el("span");
        node.append(
          el("strong", "", formatNumber(state.counts[key])),
          document.createTextNode(label),
        );
        return node;
      }),
    );
    renderGraph(state, view.agent, selectAgent);
    renderFeed();
    await renderInspector();
    $("#footer-status").textContent =
      `State reconstructed at event ${formatNumber(cursor)} · ${view.branch.parent_id ? "Changes belong to this branch" : "Original history preserved"}`;
    $("#previous").disabled = cursor === 0;
    $("#next").disabled = cursor === view.branch.head;
  } catch (error) {
    if (ticket === requestId) failure(error);
  }
}

function matches(event) {
  if (event.position > view.cursor) return false;
  if (view.agent && event.agent_id !== view.agent) return false;
  if (view.filter === "messages" && !event.kind.startsWith("message."))
    return false;
  if (view.filter === "tools" && !event.kind.startsWith("tool.")) return false;
  if (view.filter === "memory" && !event.kind.startsWith("memory."))
    return false;
  const agent = view.state.agents[event.agent_id];
  return (
    !view.query ||
    `${event.preview} ${agent?.name || ""} ${event.label}`
      .toLowerCase()
      .includes(view.query)
  );
}

function renderFeed() {
  const all = view.events.filter(matches),
    visible = all.slice(-view.limit).reverse(),
    root = $("#feed");
  root.replaceChildren();
  $("#feed-label").textContent = view.agent
    ? view.state.agents[view.agent]?.name || "Agent"
    : view.filter === "messages"
      ? "# " +
        Object.values(view.state.channels)
          .map((channel) => channel.name)
          .join(" · # ")
      : view.filter === "tools"
        ? "Recorded tool activity"
        : view.filter === "memory"
          ? "Memory revisions"
          : "Event history";
  $("#feed-count").textContent = `${formatNumber(all.length)} at cursor`;
  $("#clear-agent").hidden = !view.agent;
  if (!visible.length) {
    root.append(
      el(
        "p",
        "empty",
        "No matching events at this point. Move the timeline forward or clear your filters.",
      ),
    );
  }
  for (const event of visible) {
    const node = button(
      "",
      () => selectEvent(event).catch(failure),
      "event-row" + (view.event?.id === event.id ? " selected" : ""),
    );
    const body = el("div"),
      title = el("div", "event-title"),
      dot = el("span", "event-avatar");
    dot.style.background = event.intervention
      ? "#da8b40"
      : color(event.agent_id, view.state.agents);
    const logo = logoFor(view.state.agents[event.agent_id]);
    if (logo) {
      dot.classList.add("logo");
      const image = el("img");
      image.src = logo;
      image.alt = "";
      dot.append(image);
    }
    title.append(
      dot,
      el(
        "span",
        "",
        view.state.agents[event.agent_id]?.name ||
          (event.kind === "message.created" ? "Human" : "Environment"),
      ),
      el(
        "span",
        "event-type",
        event.intervention ? "Intervention" : event.stage_label || event.kind.split(".")[0],
      ),
    );
    body.append(
      title,
      el("div", "event-preview", event.preview || event.label),
    );
    node.append(el("span", "event-time", time(event.at)), body);
    root.append(node);
  }
  $("#more-events").hidden = all.length <= view.limit;
}

async function renderInspector() {
  if (view.event) {
    const id = view.event.id,
      branch = view.branch.id,
      cursor = view.cursor;
    const detail = await api(
      `/branches/${branch}/events/${id}?cursor=${cursor}`,
    );
    if (
      view.event?.id === id &&
      view.branch.id === branch &&
      view.cursor === cursor
    )
      inspectEvent(detail, view.state, actions);
  } else if (view.agent && view.state.agents[view.agent])
    inspectAgent(view.state.agents[view.agent], view.state, actions);
  else inspectEnvironment(view.state, actions);
}

function selectAgent(id) {
  if (!view.state.agents[id]) {
    toast("This agent is not present at the selected cursor.");
    return;
  }
  view.agent = id;
  view.event = null;
  view.limit = 45;
  renderGraph(view.state, id, selectAgent);
  renderFeed();
  renderInspector().catch(failure);
}
async function selectEvent(event) {
  view.event = event;
  renderFeed();
  await renderInspector();
}

function stopPlayback() {
  view.playing = false;
  clearTimeout(playTimer);
  $("#play").textContent = "▶ Replay";
  $("#play").setAttribute("aria-label", "Replay saved events");
}
async function playStep() {
  if (!view.playing) return;
  if (view.cursor >= view.branch.head) {
    stopPlayback();
    return;
  }
  await seek(view.cursor + Number(speedPicker.value));
  if (view.playing) playTimer = setTimeout(playStep, 450);
}

function openBranch(title, fields = [], change) {
  $('#event-menu').hidden = true;
  stopPlayback();
  liveView.pause();
  clearTimeout(seekTimer);
  const event = view.events.find(event => event.position === view.cursor);
  branchDialog({
    source: { branchId: view.branch.id, branchName: view.branch.name, cursor: view.cursor,
      at: event?.at, label: event?.resume_point ? `Before task ${event.resume_point.next_task + 1}`
        : event?.stage_label || ({ 'message.created': 'Message', 'memory.written': 'Memory recorded',
          'environment.updated': 'Shared state', 'tool.completed': 'Tool result' }[event?.kind]) || 'Selected timeline position',
      defaultName: change ? title + ' experiment' : 'Experiment ' + view.workspace.branches.filter(b => b.run_id === view.run.id).length },
    title, fields, change, live: liveView,
    created: async id => { await refreshWorkspace(); updateRunPicker(); await loadBranch(id); },
  });
}

function forkDialog() { openBranch('Fork at cursor'); }
function interventionDialog(title, fields, kind, buildData) {
  openBranch(title, fields, form => ({ kind, data: buildData(form) }));
}

function editAgent(agent) {
  const name = field("Agent name", "name", agent.name);
  name.input.required = true;
  interventionDialog(
    "Edit agent",
    [
      name,
      field("Model identifier", "model", agent.model || ""),
      field("System prompt", "prompt", agent.system_prompt || "", "textarea"),
    ],
    "agent.updated",
    (form) => ({
      id: agent.id,
      name: form.get("name"),
      model: form.get("model") || null,
      system_prompt: form.get("prompt"),
    }),
  );
}
function addAgent() {
  const id = crypto.randomUUID();
  const name = field("Agent name", "name", "New agent");
  name.input.required = true;
  interventionDialog(
    "Add agent",
    [
      name,
      field("Model identifier", "model"),
      field("System prompt", "prompt", "", "textarea"),
    ],
    "agent.added",
    (form) => ({
      id,
      name: form.get("name"),
      model: form.get("model") || null,
      system_prompt: form.get("prompt"),
    }),
  );
}
function removeAgent(agent) {
  interventionDialog(
    agent.active ? "Remove agent" : "Restore agent",
    [],
    agent.active ? "agent.removed" : "agent.updated",
    () => (agent.active ? { id: agent.id } : { id: agent.id, active: true }),
  );
}
function editGoal() {
  interventionDialog(
    "Change goal",
    [field("Shared goal", "goal", view.state.environment.goal, "textarea")],
    "environment.updated",
    (form) => ({ goal: form.get("goal") }),
  );
}

async function readMemory(memory) {
  try {
    const value = await api(
      `/branches/${view.branch.id}/memory?cursor=${view.cursor}&memory_id=${encodeURIComponent(memory.id)}`,
    );
    const text = el("div", "content-text", value.content);
    if (value.metadata?.type === "task_output") {
      text.append(button("Edit task output on a branch", () => {
        $("#dialog").close();
        interventionDialog("Edit completed task output", [field("Saved output", "content", value.content, "textarea")],
          "memory.written", form => ({ ...value, content: form.get("content") }));
      }));
    }
    openDialog("Recorded memory", text, { kicker: "MEMORY AT CURSOR" });
  } catch (error) {
    failure(error);
  }
}
async function readArtifact(digest) {
  try {
    const value = await api(`/artifacts/${digest}`),
      text = el("pre", "metadata-box", JSON.stringify(value, null, 2));
    text.style.maxHeight = "55vh";
    openDialog("Recorded model response", text, { kicker: "SOURCE ARTIFACT" });
  } catch (error) {
    failure(error);
  }
}

function compareDialog() {
  stopPlayback();
  const content = el("div"),
    branches = view.workspace.branches.filter(
      (branch) => branch.run_id === view.run.id && branch.id !== view.branch.id,
    );
  const choice = field("Reference branch", "reference", "", "select");
  choice.input.setOptions(branches.map(branch => ({ value: branch.id, label: branch.name,
    description: `${branch.head} events · ${branch.parent_id ? `forked at event ${branch.fork_position}` : 'original conversation'}` })));
  choice.input.value = view.branch.parent_id || branches[0]?.id;
  content.append(
    el(
      "p",
      "",
      `Current branch: ${view.branch.name}, event ${view.cursor}. A parent branch is compared at the fork point; other branches at the nearest available cursor.`,
    ),
    choice.fragment,
  );
  const result = el("div");
  content.append(result);
  const update = async () => {
    const reference = branches.find(
      (branch) => branch.id === choice.input.value,
    );
    if (!reference) return;
    const cursor =
      reference.id === view.branch.parent_id
        ? view.branch.fork_position
        : Math.min(view.cursor, reference.head);
    const diff = await api(
      `/compare?left=${reference.id}&right=${view.branch.id}&left_cursor=${cursor}&right_cursor=${view.cursor}`,
    );
    result.replaceChildren();
    let changes = 0;
    for (const group of [
      "agents",
      "channels",
      "messages",
      "memories",
      "tools",
    ]) {
      const entry = diff[group];
      if (entry.added.length || entry.removed.length || entry.changed.length) {
        changes++;
        result.append(
          el(
            "div",
            "diff-change",
            `${group}: ${entry.added.length} added · ${entry.removed.length} removed · ${entry.changed.length} changed`,
          ),
        );
      }
      if (group === "agents" || group === "channels")
        for (const change of entry.changed) {
          const block = el("div", "diff-change");
          block.append(el("strong", "", change.after.name || change.id));
          for (const key of Object.keys(change.after)) {
            if (
              JSON.stringify(change.before[key]) !==
              JSON.stringify(change.after[key])
            )
              block.append(
                el(
                  "pre",
                  "",
                  `${key}\nBefore: ${JSON.stringify(change.before[key])}\nAfter: ${JSON.stringify(change.after[key])}`,
                ),
              );
          }
          result.append(block);
        }
    }
    if (
      JSON.stringify(diff.environment.before) !==
      JSON.stringify(diff.environment.after)
    ) {
      changes++;
      const block = el("div", "diff-change");
      block.append(
        el("strong", "", "Environment changed"),
        el(
          "pre",
          "",
          `Before: ${diff.environment.before.goal}\nAfter: ${diff.environment.after.goal}`,
        ),
      );
      result.append(block);
    }
    if (!changes)
      result.append(
        el("p", "", "The reconstructed states are identical at these cursors."),
      );
  };
  choice.input.onchange = () => update().catch(failure);
  openDialog("Compare trajectories", content, { kicker: "BRANCH DIFF" });
  update().catch(failure);
}

$("#fork-button").onclick = forkDialog;
$("#read-task").onclick = () => openDialog("Benchmark question", el("div", "content-text", view.state.environment.task),
  { kicker: view.run.metadata.task_id || "RECORDED TASK" });
$("#new-branch").onclick = forkDialog;
$("#cursor").addEventListener("input", (event) => {
  liveView.pause();
  stopPlayback();
  clearTimeout(seekTimer);
  seekTimer = setTimeout(() => seek(Number(event.target.value)), 70);
});
$("#previous").onclick = () => {
  liveView.pause();
  stopPlayback();
  seek(view.cursor - 1);
};
$("#next").onclick = () => {
  liveView.pause();
  stopPlayback();
  seek(view.cursor + 1);
};
$("#go-end").onclick = () => {
  stopPlayback();
  seek(view.branch.head);
};
$("#play").onclick = () => {
  liveView.pause();
  if (view.playing) {
    stopPlayback();
    return;
  }
  view.playing = true;
  $("#play").textContent = "Ⅱ Pause";
  $("#play").setAttribute("aria-label", "Pause timeline");
  if (view.cursor >= view.branch.head) view.cursor = 0;
  playStep();
};
$$("[data-filter]").forEach(
  (tab) =>
    (tab.onclick = () => {
      view.filter = tab.dataset.filter;
      view.limit = 45;
      $$("[data-filter]").forEach((other) => {
        other.classList.toggle("active", other === tab);
        other.setAttribute("aria-selected", String(other === tab));
      });
      renderFeed();
    }),
);
$("#search").oninput = (event) => {
  view.query = event.target.value.trim().toLowerCase();
  renderFeed();
};
$("#more-events").onclick = () => {
  view.limit += 45;
  renderFeed();
};
$("#clear-agent").onclick = () => {
  view.agent = null;
  view.event = null;
  renderGraph(view.state, null, selectAgent);
  renderFeed();
  renderInspector().catch(failure);
};
$("#add-agent").onclick = addAgent;
$("#edit-goal").onclick = editGoal;
$("#compare").onclick = compareDialog;
$("#analyze").onclick = async () => {
  try {
    stopPlayback();
    const result = await post(`/branches/${view.branch.id}/analyses`, {
      plugin_id: "activity",
      cursor: view.cursor,
    });
    view.event = null;
    inspectAnalysis(result, view.state);
    toast("Analysis saved with its input history and plugin version.");
  } catch (error) {
    failure(error);
  }
};
$("#checkpoint").onclick = async () => {
  try {
    const result = await post(`/branches/${view.branch.id}/checkpoint`, {
      cursor: view.cursor,
    });
    toast(`Git checkpoint saved · ${result.commit.slice(0, 10)}`);
  } catch (error) {
    failure(error);
  }
};
runPicker.onchange = (event) => {
  const branch = view.workspace.branches.find(
    (branch) => branch.run_id === event.target.value && !branch.parent_id,
  );
  if (branch) loadBranch(branch.id).catch(failure);
};

const resize = $("#resize-inspector");
resize.onpointerdown = (event) => {
  resize.setPointerCapture(event.pointerId);
};
resize.onpointermove = (event) => {
  if (resize.hasPointerCapture(event.pointerId))
    document.documentElement.style.setProperty(
      "--inspector",
      Math.max(240, Math.min(600, window.innerWidth - event.clientX)) + "px",
    );
};
resize.onkeydown = (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const width = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue("--inspector"),
  );
  document.documentElement.style.setProperty(
    "--inspector",
    Math.max(
      240,
      Math.min(600, width + (event.key === "ArrowLeft" ? 20 : -20)),
    ) + "px",
  );
};
new ResizeObserver(() => {
  if (view.state) renderGraph(view.state, view.agent, selectAgent);
}).observe($("#graph"));

async function timelineEvent(event, point, context) {
  liveView.pause();
  stopPlayback();
  view.event = event;
  await seek(event.position);
  if (view.cursor !== event.position) return;
  const menu = $("#event-menu"),
    agent = view.state.agents[event.agent_id];
  menu.replaceChildren();
  const header = el("div", "event-menu-header"),
    logo = logoFor(agent);
  if (logo) {
    const image = el("img");
    image.src = logo;
    image.alt = "";
    header.append(image);
  }
  header.append(
    el(
      "strong",
      "",
      agent?.name ||
        (event.kind === "message.created" ? "Human" : "Environment"),
    ),
  );
  menu.append(
    header,
    el("time", "", `${time(event.at)} UTC · event ${event.position}`),
    el("p", "", event.preview || event.label),
  );
  const options = el("div", "menu-actions");
  const item = (label, action, className = "") =>
    options.append(
      button(
        label,
        () => {
          menu.hidden = true;
          action();
        },
        className,
      ),
    );
  item("Inspect event", () => {
    view.event = event;
    renderInspector().catch(failure);
    $(".inspector").scrollIntoView({ block: "nearest" });
  });
  item("⑂  Create branch here", forkDialog, "menu-primary");
  item("▶  Run from here…", () => openBranch("Run from here"));
  if (agent) {
    item("Change agent prompt…", () => editAgent(agent));
    item(agent.active ? "Remove this agent…" : "Restore this agent…", () =>
      removeAgent(agent),
    );
  }
  item("Add an agent…", addAgent);
  item("Change shared goal…", editGoal);
  menu.append(options);
  menu.hidden = false;
  menu.style.left =
    Math.max(8, Math.min(point.x, window.innerWidth - menu.offsetWidth - 12)) +
    "px";
  menu.style.top =
    Math.max(
      8,
      Math.min(point.y + 10, window.innerHeight - menu.offsetHeight - 12),
    ) + "px";
  if (context) options.querySelector("button").focus();
}
function showPointMenu(point) {
  const menu = $('#event-menu');
  menu.replaceChildren(el('strong', '', `Event ${view.cursor} · ${time(view.state.occurred_at)} UTC`));
  const options = el('div', 'menu-actions');
  for (const [label, action] of [
    ['Create branch here', forkDialog], ['Run from here…', () => openBranch('Run from here')],
    ['Change shared goal…', editGoal],
  ]) options.append(button(label, () => { menu.hidden = true; action(); }));
  menu.append(options);
  menu.hidden = false;
  menu.style.left = Math.max(8, Math.min(point.x, innerWidth - menu.offsetWidth - 12)) + 'px';
  menu.style.top = Math.max(8, Math.min(point.y + 10, innerHeight - menu.offsetHeight - 12)) + 'px';
}

document.addEventListener("pointerdown", (event) => {
  if (
    !event.target.closest("#event-menu") &&
    !event.target.closest(".time-event")
  )
    $("#event-menu").hidden = true;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("#event-menu").hidden = true;
});

async function boot(selectedBranchId) {
  const route = selectedBranchId ? {} : readWorkspaceRoute();
  await refreshWorkspace();
  liveView.enable(view.workspace.capabilities.live?.enabled);
  const renderers = { mast: installMast };
  for (const manifest of view.workspace.capabilities.web_plugins || []) {
    if (installedPlugins.has(manifest.id)) continue;
    renderers[manifest.ui?.renderer]?.(manifest, {
      selection,
      registerView: (config) => workspaceViews.register(config),
      openView: openWorkspaceView,
      openReport: async (job) => {
        if (view.branch?.id !== job.branch_id) await loadBranch(job.branch_id, job.cursor);
        openWorkspaceView("mast", { report: job.id });
      },
      setViewParams: (id, params) => {
        if (workspaceViews.current !== id) return;
        workspaceViews.entries.get(id).params = params;
        saveWorkspaceRoute();
      },
      showSnapshot: async (branchId, cursor) => {
        if (branchId !== view.branch?.id) await loadBranch(branchId, cursor);
        else await seek(cursor);
        openWorkspaceView("timeline");
      },
      showEvidenceEvent: async (branchId, cursor, eventId) => {
        liveView.pause();
        stopPlayback();
        if (branchId !== view.branch?.id) await loadBranch(branchId, cursor);
        else await seek(cursor);
        if (view.branch?.id !== branchId || view.cursor !== cursor) return;
        const event = view.events.find(item => item.id === eventId);
        if (!event) throw new Error("This evidence event is unavailable in the selected history.");
        openWorkspaceView("timeline");
        await selectEvent(event);
      },
      onImport: async (branchId) => {
        await boot(branchId);
        $('[data-filter="all"]').click();
      },
    });
    installedPlugins.add(manifest.id);
  }
  if (!view.workspace.runs.length) {
    $("#task-name").textContent = "No runs yet";
    $("#footer-status").textContent =
      "Build an application with the framework and supply its first run.";
    return;
  }
  updateRunPicker();
  $("#checkpoint").disabled = !view.workspace.capabilities.git;
  await loadBranch(
    selectedBranchId || route.branchId || view.workspace.branches.find((branch) =>
      !branch.parent_id && branch.run_id === view.workspace.runs[0].id).id,
    route.cursor,
  );
  if (route.view && route.view !== "timeline") openWorkspaceView(route.view, { report: route.report }, { replace: true });
}
boot().catch(failure);

// Discover captures started by another local process without disturbing the selected cursor.
setInterval(async () => {
  if (!view.workspace?.capabilities.live?.enabled) return;
  try {
    const previous = new Set(view.workspace.runs.map(run => run.id));
    await refreshWorkspace();
    if (view.workspace.runs.some(run => !previous.has(run.id))) updateRunPicker();
    if (!view.branch && view.workspace.runs.length) await boot();
  } catch (_) { /* The live status handles disconnection; do not spam toasts. */ }
}, 5000);
