import { $, el, button, api, post, toast, failure, formatNumber, time } from "./ui.js";
import { renderMarkdownInto } from "./markdown.js";
import { EventTimeline } from "./timeline.js";
import { Transcript } from "./transcript.js";
import { renderInspector } from "./inspect.js";
import { installCompare } from "./compare.js";
import { installMast } from "./mast.js";
import { field, openDialog } from "./dialog.js";
import { branchDialog } from "./branch.js";
import { Picker } from "./components.js?v=1";
import { WorkspaceViews, workspaceURL, readWorkspaceRoute } from "./workspace.js";
import { LiveView } from "./live.js";

const view = {
  workspace: null,
  run: null,
  branch: null,
  events: [],
  state: null,
  cursor: 0,
  selection: { type: "overview" },
  agentFilter: null,
  playing: false,
};
const details = new Map();
const timelines = new Map();
const menuActions = [];
let seekTicket = 0, inspectorTicket = 0, seekTimer, playTimer;

// A set of ids kept in this browser; favorites stay personal until runs carry shared metadata.
function browserSet(key) {
  let ids = new Set();
  try { ids = new Set(JSON.parse(localStorage.getItem(key) || "[]")); } catch { /* Storage unavailable; the set lasts for this visit. */ }
  return {
    has: (id) => ids.has(id),
    toggle(id) {
      if (!ids.delete(id)) ids.add(id);
      try { localStorage.setItem(key, JSON.stringify([...ids])); } catch { /* Storage unavailable; the set lasts for this visit. */ }
    },
  };
}
const favoriteRuns = browserSet("swarm-lens:favorite-runs");

const runPicker = new Picker({ id: "conversation", label: "Run", heading: "Runs", searchable: true, hideLabel: true,
  placeholder: "Search runs…", favorites: favoriteRuns });
$("#run-select").append(runPicker.root);
runPicker.mount();

const timeline = new EventTimeline($("#timeline"), {
  onSeek: (position) => userSeek(position),
  onSelect: (event) => selectEvent(event),
  onContext: (event, position, point) => openContextMenu(event, position, point),
  onAgent: (id) => selectAgent(id),
  onPlay: () => togglePlayback(),
});
const transcript = new Transcript($("#transcript"), {
  onSelect: (event) => selectEvent(event),
  loadDetail: (event) => loadDetail(view.branch.id, event),
  onClearAgent: () => filterAgent(null),
});
const workspaceViews = new WorkspaceViews((id) => openWorkspaceView(id, workspaceViews.entries.get(id).params));
workspaceViews.register({ id: "timeline", title: "Timeline", panel: $("#workspace-timeline") });

const liveView = new LiveView({
  selection: () => view.branch && { branchId: view.branch.id, cursor: view.cursor, head: view.branch.head,
    parentId: view.branch.parent_id, branchName: view.branch.name, runName: view.run.name },
  seek: (cursor) => seek(cursor),
  forked: async (id) => { await refreshWorkspace(); await loadBranch(id); },
  update: applyLiveEvents,
});

// Data access

function loadDetail(branchId, event) {
  const key = `${branchId}:${event.id}`;
  if (!details.has(key)) {
    const head = view.workspace.branches.find((branch) => branch.id === branchId)?.head ?? event.position;
    const request = api(`/branches/${branchId}/events/${event.id}?cursor=${Math.max(head, event.position)}`);
    request.catch(() => details.delete(key));
    details.set(key, request);
  }
  return details.get(key);
}

function loadTimeline(branchId) {
  if (branchId === view.branch?.id) return Promise.resolve({ branch: view.branch, events: view.events });
  if (!timelines.has(branchId)) {
    const request = api(`/branches/${branchId}/timeline`);
    request.catch(() => timelines.delete(branchId));
    timelines.set(branchId, request);
  }
  return timelines.get(branchId);
}

async function refreshWorkspace() {
  view.workspace = await api("/workspace");
  timelines.clear();
  updateRunPicker();
}

const runBranches = (runId = view.run?.id) => view.workspace.branches.filter((branch) => branch.run_id === runId);
const rootBranch = (runId) => runBranches(runId).find((branch) => !branch.parent_id);

// URL routing

function saveRoute(replace = true) {
  if (!view.branch) return;
  const url = workspaceURL({ branchId: view.branch.id, cursor: view.cursor,
    view: workspaceViews.current || "timeline", ...workspaceViews.params });
  if (url !== location.href) history[replace ? "replaceState" : "pushState"](null, "", url);
}

function openWorkspaceView(id, params = {}, { replace = false, write = true } = {}) {
  stopPlayback();
  closeMenu();
  workspaceViews.show(id, params);
  $("#fork-button").hidden = id !== "timeline";
  if (write) saveRoute(replace);
}

async function restoreRoute() {
  const route = readWorkspaceRoute();
  if (route.branchId && route.branchId !== view.branch?.id) await loadBranch(route.branchId, route.cursor);
  else if (route.cursor !== undefined && route.cursor !== view.cursor) await seek(route.cursor);
  openWorkspaceView(route.view, route.params, { replace: true });
}
window.addEventListener("hashchange", () => restoreRoute().catch(failure));

// App bar

function updateRunPicker() {
  runPicker.setOptions(view.workspace.runs.map((run) => {
    const count = runBranches(run.id).length;
    return { value: run.id, label: run.name,
      badge: run.metadata.framework?.toUpperCase(),
      description: `${time(run.created_at)} UTC · ${count} branch${count === 1 ? "" : "es"}` };
  }));
  if (view.run) runPicker.value = view.run.id;
}
runPicker.onchange = (event) => {
  const branch = rootBranch(event.target.value);
  if (branch && branch.id !== view.branch?.id) loadBranch(branch.id).catch(failure);
};

function renderAppBar() {
  $("#branch-switch .label").textContent = view.branch.name;
  $("#branch-switch .count").textContent = runBranches().length > 1 ? `${runBranches().length} branches` : "";
}

function branchMenuItems() {
  const branches = runBranches();
  const depth = (branch) => {
    let count = 0;
    for (let parent = branch; parent?.parent_id; count++) parent = branches.find((item) => item.id === parent.parent_id);
    return count;
  };
  const ordered = [];
  const visit = (parentId) => branches.filter((branch) => (branch.parent_id || null) === parentId)
    .sort((a, b) => (a.fork_position ?? 0) - (b.fork_position ?? 0))
    .forEach((branch) => { ordered.push(branch); visit(branch.id); });
  visit(null);
  return [
    { heading: "Branches" },
    ...ordered.map((branch) => ({
      label: `${branch.parent_id ? "↳ " : ""}${branch.name}`,
      indent: Math.min(depth(branch), 4),
      hint: branch.parent_id ? `at ${formatNumber(branch.fork_position)} · +${formatNumber(branch.head - branch.fork_position)}`
        : `${formatNumber(branch.head)} events`,
      current: branch.id === view.branch.id,
      onClick: () => loadBranch(branch.id),
    })),
    "---",
    { label: "Fork at the selected event…", hint: "F", onClick: forkDialog },
  ];
}

// Plugins contribute actions under their own name, in registration order; core actions follow.
function pluginMenuItems() {
  const plugins = [...new Set(menuActions.map((action) => action.plugin))];
  if (!plugins.length) return [];
  return [{ heading: "Plugins" }, ...plugins.flatMap((plugin) => [
    { subheading: plugin }, ...menuActions.filter((action) => action.plugin === plugin),
  ]), "---"];
}

function moreMenuItems() {
  return [
    ...pluginMenuItems(),
    ...(view.workspace.capabilities.git ? [{ label: "Save Git checkpoint", onClick: saveCheckpoint }] : []),
    ...(view.run?.metadata.dataset_url ? [{ label: "Source and provenance ↗", onClick: () => window.open(view.run.metadata.dataset_url, "_blank", "noopener") }] : []),
    "---",
    { heading: "Theme" },
    ...[["system", "System"], ["light", "Light"], ["dark", "Dark"]].map(([value, label]) => ({
      label, current: window.swarmLensTheme.get() === value, onClick: () => window.swarmLensTheme.set(value),
    })),
  ];
}

// Menus

function closeMenu() {
  const menu = $("#menu");
  if (menu.hidden) return;
  menu.hidden = true;
  menu.anchor?.setAttribute("aria-expanded", "false");
}

function openMenu(items, { anchor, point }) {
  const menu = $("#menu");
  closeMenu();
  menu.replaceChildren(...items.map((item) => {
    if (item === "---") return el("hr");
    if (item.heading) return el("div", "menu-label", item.heading);
    if (item.subheading) return el("div", "menu-sublabel", item.subheading);
    const node = button("", () => { closeMenu(); item.onClick(); });
    node.setAttribute("role", "menuitem");
    node.append(el("span", "", item.label));
    if (item.hint) node.append(el("span", "hint", item.hint));
    if (item.current) node.setAttribute("aria-current", "true");
    if (item.indent) node.style.paddingLeft = `${10 + item.indent * 14}px`;
    return node;
  }));
  menu.anchor = anchor;
  menu.hidden = false;
  const rect = anchor?.getBoundingClientRect();
  const x = rect ? Math.min(rect.left, innerWidth - menu.offsetWidth - 8) : point.x;
  const y = rect ? rect.bottom + 4 : point.y + 6;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  anchor?.setAttribute("aria-expanded", "true");
  menu.querySelector("button")?.focus();
}

function toggleMenu(anchor, items) {
  if (!$("#menu").hidden && $("#menu").anchor === anchor) closeMenu();
  else openMenu(items(), { anchor });
}
$("#branch-switch").onclick = (event) => toggleMenu(event.currentTarget, branchMenuItems);
$("#more-button").onclick = (event) => toggleMenu(event.currentTarget, moreMenuItems);

async function openContextMenu(event, position, point) {
  stopPlayback();
  liveView.pause();
  if (event) await selectEvent(event);
  else await seek(position);
  const agent = event && view.state.agents[event.agent_id];
  openMenu([
    { heading: `Event ${formatNumber(view.cursor)} · ${time(view.state.occurred_at)}` },
    { label: "Fork here…", hint: "F", onClick: forkDialog },
    ...(agent ? [
      { label: `Change ${agent.name}'s prompt…`, onClick: () => editAgent(agent) },
      { label: `${agent.active ? "Remove" : "Restore"} ${agent.name}…`, onClick: () => removeAgent(agent) },
    ] : []),
    { label: "Change goal…", onClick: editGoal },
  ], { point });
}

document.addEventListener("pointerdown", (event) => {
  if (!event.target.closest("#menu") && !event.target.closest("[aria-haspopup]")) closeMenu();
});

// Navigation

async function loadBranch(id, cursor) {
  stopPlayback();
  const data = await api(`/branches/${id}/timeline`);
  workspaceViews.reset();
  openWorkspaceView("timeline", {}, { write: false });
  view.branch = data.branch;
  view.events = data.events;
  view.run = view.workspace.runs.find((run) => run.id === view.branch.run_id);
  view.selection = { type: "overview" };
  view.agentFilter = null;
  runPicker.value = view.run.id;
  renderAppBar();
  timeline.setData(view.events, view.branch);
  await seek(cursor ?? view.branch.head);
  liveView.connect(view.branch, view.run.metadata.source_type === "live" && cursor === undefined);
}

function userSeek(position) {
  liveView.pause();
  stopPlayback();
  clearTimeout(seekTimer);
  seekTimer = setTimeout(() => seek(position, { moveSelection: true }).catch(failure), 30);
}

async function seek(cursor, { moveSelection = false } = {}) {
  cursor = Math.max(0, Math.min(view.branch.head, Number(cursor)));
  view.cursor = cursor;
  saveRoute();
  timeline.setCursor(cursor, true);
  const ticket = ++seekTicket;
  const state = await api(`/branches/${view.branch.id}/state?cursor=${cursor}`);
  if (ticket !== seekTicket) return;
  view.state = state;
  const atCursor = view.events.find((event) => event.position === cursor);
  if (moveSelection && view.selection.type === "event" && atCursor) view.selection = { type: "event", event: atCursor };
  const { selection } = view;
  if (selection.type === "event" && selection.event.position > cursor) view.selection = { type: "overview" };
  if (selection.type === "agent" && !state.agents[selection.agentId]) view.selection = { type: "overview" };
  if (view.agentFilter && !state.agents[view.agentFilter]) view.agentFilter = null;
  renderExplorer();
}

function renderExplorer() {
  const selectedId = view.selection.type === "event" ? view.selection.event.id : null;
  timeline.select(selectedId);
  transcript.render({ events: view.events, agents: view.state.agents, cursor: view.cursor, selectedId,
    agentId: view.agentFilter });
  renderSelection().catch(failure);
}

async function renderSelection() {
  const ticket = ++inspectorTicket;
  let selection = view.selection;
  if (selection.type === "event") {
    const detail = await loadDetail(view.branch.id, selection.event);
    if (ticket !== inspectorTicket) return;
    selection = { ...selection, detail };
  }
  renderInspector($("#inspector"), selection, { state: view.state, run: view.run, branch: view.branch,
    cursor: view.cursor, actions: inspectorActions });
}

async function selectEvent(event) {
  liveView.pause();
  stopPlayback();
  view.selection = { type: "event", event };
  if (event.position !== view.cursor) await seek(event.position);
  else renderExplorer();
}

function selectAgent(agentId) {
  if (!view.state.agents[agentId]) return toast("This agent is not present at the selected event.");
  view.selection = { type: "agent", agentId };
  filterAgent(agentId);
}

function filterAgent(agentId) {
  view.agentFilter = agentId;
  if (!agentId && view.selection.type === "agent") view.selection = { type: "overview" };
  renderExplorer();
}

function clearSelection() {
  view.selection = { type: "overview" };
  view.agentFilter = null;
  renderExplorer();
}

// Playback

function stopPlayback() {
  if (!view.playing) return;
  view.playing = false;
  clearTimeout(playTimer);
  timeline.setPlaying(false);
}

function togglePlayback() {
  liveView.pause();
  if (view.playing) return stopPlayback();
  view.playing = true;
  timeline.setPlaying(true);
  const start = view.cursor >= view.branch.head ? 0 : view.cursor;
  seek(start).then(playStep).catch(failure);
}

async function playStep() {
  if (!view.playing) return;
  if (view.cursor >= view.branch.head) return stopPlayback();
  await seek(view.cursor + 1);
  if (view.playing) playTimer = setTimeout(playStep, 350);
}

// Live updates arrive in order; a gap means the stream must reconnect.
async function applyLiveEvents(data, follow) {
  if (data.branch.id !== view.branch?.id) return;
  const events = data.events.filter((event) => event.position > view.events.length);
  if (!events.length) return;
  if (events[0].position !== view.events.length + 1) throw new Error("Live event gap; reconnecting to recover history.");
  view.events.push(...events);
  view.branch = { ...data.branch, head: data.cursor };
  const stored = view.workspace.branches.find((branch) => branch.id === view.branch.id);
  if (stored) Object.assign(stored, view.branch);
  timelines.delete(view.branch.id);
  timeline.setData(view.events, view.branch);
  renderAppBar();
  await seek(follow ? view.branch.head : view.cursor);
}

// Branching and interventions

function forkPointLabel(event) {
  if (event?.resume_point) return `Before task ${event.resume_point.next_task + 1}`;
  return event?.stage_label || "Selected event";
}

function openBranch(title, fields = [], change) {
  closeMenu();
  stopPlayback();
  liveView.pause();
  clearTimeout(seekTimer);
  const event = view.events.find((item) => item.position === view.cursor);
  branchDialog({
    source: { branchId: view.branch.id, branchName: view.branch.name, cursor: view.cursor, at: event?.at,
      label: forkPointLabel(event), defaultName: change ? `${title} experiment` : `Experiment ${runBranches().length}` },
    title, fields, change, live: liveView,
    created: async (id) => { await refreshWorkspace(); await loadBranch(id); },
  });
}

const forkDialog = () => openBranch("Fork here");
const intervene = (title, fields, kind, buildData) => openBranch(title, fields, (form) => ({ kind, data: buildData(form) }));

function agentFields(agent = {}) {
  const name = field("Name", "name", agent.name || "New agent");
  name.input.required = true;
  return [name, field("Model", "model", agent.model || ""), field("System prompt", "prompt", agent.system_prompt || "", "textarea")];
}
const agentData = (id, form) => ({ id, name: form.get("name"), model: form.get("model") || null, system_prompt: form.get("prompt") });

function editAgent(agent) {
  intervene(`Change ${agent.name}`, agentFields(agent), "agent.updated", (form) => agentData(agent.id, form));
}

function addAgent() {
  const id = crypto.randomUUID();
  intervene("Add agent", agentFields(), "agent.added", (form) => agentData(id, form));
}

function removeAgent(agent) {
  intervene(agent.active ? `Remove ${agent.name}` : `Restore ${agent.name}`, [],
    agent.active ? "agent.removed" : "agent.updated", () => agent.active ? { id: agent.id } : { id: agent.id, active: true });
}

function editGoal() {
  intervene("Change goal", [field("Shared goal", "goal", view.state.environment.goal || "", "textarea")],
    "environment.updated", (form) => ({ goal: form.get("goal") }));
}

// Reading recorded content

function markdownBlock(text) {
  const node = el("div");
  renderMarkdownInto(node, text).catch(failure);
  return node;
}

function readTask() {
  openDialog("Task", markdownBlock(view.state.environment.task || view.state.environment.goal || ""), { kicker: view.run.name });
}

async function readMemory(memory) {
  try {
    const value = await api(`/branches/${view.branch.id}/memory?cursor=${view.cursor}&memory_id=${encodeURIComponent(memory.id)}`);
    const content = el("div", "content-text", value.content);
    if (value.metadata?.type === "task_output") {
      content.append(button("Edit this output on a new branch", () => {
        $("#dialog").close();
        intervene("Edit task output", [field("Saved output", "content", value.content, "textarea")],
          "memory.written", (form) => ({ ...value, content: form.get("content") }));
      }));
    }
    openDialog("Memory", content, { kicker: `At event ${formatNumber(view.cursor)}` });
  } catch (error) { failure(error); }
}

async function readArtifact(digest) {
  try {
    const value = await api(`/artifacts/${digest}`);
    openDialog("Recorded model response", el("pre", "metadata-box", JSON.stringify(value, null, 2)), { kicker: digest.slice(0, 12) });
  } catch (error) { failure(error); }
}

async function runActivityAnalysis() {
  try {
    stopPlayback();
    const record = await post(`/branches/${view.branch.id}/analyses`, { plugin_id: "activity", cursor: view.cursor });
    view.selection = { type: "analysis", record };
    renderSelection().catch(failure);
  } catch (error) { failure(error); }
}

async function saveCheckpoint() {
  try {
    const result = await post(`/branches/${view.branch.id}/checkpoint`, { cursor: view.cursor });
    toast(`Checkpoint saved · ${result.commit.slice(0, 10)}`);
  } catch (error) { failure(error); }
}

const inspectorActions = { fork: forkDialog, editAgent, removeAgent, addAgent, editGoal, selectAgent, clearSelection,
  readMemory, readArtifact, readTask };

$("#fork-button").onclick = forkDialog;

// Layout: both splitters persist their size in this browser.

function resizable(key, { property, target, axis, measure, min, max, invert = false }) {
  const handle = $(`#resize-${key}`);
  const storageKey = `swarm-lens:${key}`;
  const apply = (size) => {
    size = Math.round(Math.max(min(), Math.min(max(), size)));
    target.style.setProperty(property, `${size}px`);
    try { localStorage.setItem(storageKey, String(size)); } catch { /* The size still applies for this visit. */ }
  };
  try {
    const saved = Number(localStorage.getItem(storageKey));
    if (saved) apply(saved);
  } catch { /* Browser storage can be unavailable. */ }
  const pointer = (event) => (axis === "y" ? event.clientY : event.clientX);
  let origin;
  handle.onpointerdown = (event) => {
    handle.setPointerCapture(event.pointerId);
    origin = { pointer: pointer(event), size: measure() };
  };
  handle.onpointermove = (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    const delta = pointer(event) - origin.pointer;
    apply(origin.size + (invert ? -delta : delta));
  };
  const [shrink, grow] = axis === "y" ? ["ArrowUp", "ArrowDown"] : invert ? ["ArrowRight", "ArrowLeft"] : ["ArrowLeft", "ArrowRight"];
  handle.onkeydown = (event) => {
    if (event.key !== shrink && event.key !== grow) return;
    event.preventDefault();
    apply(measure() + (event.key === grow ? 24 : -24));
  };
}
resizable("timeline", { property: "--timeline-h", target: $(".explorer"), axis: "y",
  measure: () => $("#timeline").getBoundingClientRect().height, min: () => 160, max: () => innerHeight - 220 });
resizable("inspector", { property: "--inspector-w", target: document.documentElement, axis: "x", invert: true,
  measure: () => $("#inspector").getBoundingClientRect().width, min: () => 300, max: () => Math.min(720, innerWidth - 420) });

// Keyboard

const typing = (target) => target.closest("input, textarea, select, [contenteditable], dialog");
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!$("#menu").hidden) return closeMenu();
    if (!typing(event.target) && view.selection.type !== "overview") return clearSelection();
  }
  if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey || !view.branch) return;
  if (workspaceViews.current !== "timeline") return;
  if (event.key === "f" || event.key === "F") { event.preventDefault(); forkDialog(); }
  if (document.activeElement === document.body && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
    event.preventDefault();
    userSeek(view.cursor + (event.key === "ArrowLeft" ? -1 : 1));
  }
});

// Startup

function pluginHost(manifest) {
  return {
    selection: () => view.branch ? { branchId: view.branch.id, cursor: view.cursor, name: view.run.name,
      branchName: view.branch.name } : null,
    registerView: (config) => workspaceViews.register(config),
    openView: openWorkspaceView,
    openReport: async (job) => {
      if (view.branch?.id !== job.branch_id) await loadBranch(job.branch_id, job.cursor);
      openWorkspaceView("mast", { report: job.id });
    },
    setViewParams: (id, params) => {
      if (workspaceViews.current !== id) return;
      workspaceViews.entries.get(id).params = params;
      saveRoute();
    },
    showSnapshot: (branchId, cursor) => openTimelineAt(branchId, cursor),
    showEvidenceEvent: async (branchId, cursor, eventId) => {
      await openTimelineAt(branchId, cursor);
      const event = view.events.find((item) => item.id === eventId);
      if (!event) throw new Error("This evidence event is not in the selected history.");
      await selectEvent(event);
    },
    onImport: async (branchId) => { await refreshWorkspace(); await loadBranch(branchId); },
    addAction: (action) => menuActions.push({ ...action, plugin: manifest.title }),
  };
}

async function openTimelineAt(branchId, cursor) {
  if (branchId !== view.branch?.id) await loadBranch(branchId, cursor);
  else await seek(cursor);
  openWorkspaceView("timeline");
}

let pluginsInstalled = false;
function installPlugins() {
  if (pluginsInstalled) return;
  pluginsInstalled = true;
  const renderers = { mast: installMast };
  installCompare({
    registerView: (config) => workspaceViews.register(config),
    context: () => ({ workspace: view.workspace, run: view.run, branch: view.branch, cursor: view.cursor }),
    loadTimeline,
    loadDetail,
    openTimeline: (branchId, cursor) => openTimelineAt(branchId, cursor).catch(failure),
    setViewParams: (params) => {
      if (workspaceViews.current !== "compare") return;
      workspaceViews.entries.get("compare").params = params;
      saveRoute();
    },
    favoriteRuns,
  });
  for (const manifest of view.workspace.capabilities.web_plugins || []) {
    renderers[manifest.ui?.renderer]?.(manifest, pluginHost(manifest));
  }
  if (view.workspace.capabilities.plugins?.some((plugin) => plugin.id === "activity")) {
    menuActions.push({ plugin: "Activity", label: "Activity summary", onClick: runActivityAnalysis });
  }
}

function showEmptyWorkspace() {
  $("#transcript").replaceChildren(el("p", "empty", "No runs yet. Import a trace or connect a framework to record one."));
  $("#fork-button").disabled = true;
}

async function boot() {
  const route = readWorkspaceRoute();
  await refreshWorkspace();
  liveView.enable(view.workspace.capabilities.live?.enabled);
  installPlugins();
  if (!view.workspace.runs.length) return showEmptyWorkspace();
  const branchId = route.branchId || rootBranch(view.workspace.runs[0].id).id;
  await loadBranch(branchId, route.cursor);
  if (route.view !== "timeline") openWorkspaceView(route.view, route.params, { replace: true });
}
boot().catch(failure);

// Captures started by another local process appear without moving the selected cursor.
setInterval(async () => {
  if (!view.workspace?.capabilities.live?.enabled) return;
  try {
    const known = new Set(view.workspace.runs.map((run) => run.id));
    const workspace = await api("/workspace");
    if (workspace.runs.every((run) => known.has(run.id))) return;
    await refreshWorkspace();
    if (!view.branch) await boot();
  } catch { /* The live pill reports disconnection. */ }
}, 5000);
