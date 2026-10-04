import { el, button, avatar, color, eventTone, speakerName, time, formatNumber, failure } from "./ui.js";
import { renderMarkdownInto } from "./markdown.js";
import { topologyGraph } from "./graph.js";
import { interventionLabel } from "./transcript.js";

const LONG_TASK = 400;

export function renderInspector(root, selection, ctx) {
  const view = {
    overview: overviewView,
    agent: agentView,
    event: eventView,
    analysis: analysisView,
  }[selection.type];
  const content = el("div", "ins");
  view(content, selection, ctx);
  const key = selectionKey(selection);
  const scroll = root.dataset.selection === key ? root.scrollTop : 0;
  root.replaceChildren(content);
  root.dataset.selection = key;
  root.scrollTop = scroll;
}

// Re-renders of the same selection (cursor steps, live updates) keep the reader's scroll position.
function selectionKey(selection) {
  return [selection.type, selection.agentId, selection.event?.id, selection.record?.cursor].join(":");
}

function section(title, ...children) {
  const node = el("section", "ins-section");
  node.append(el("h3", "section-title", title), ...children);
  return node;
}

function closeButton(actions) {
  const node = button("×", actions.clearSelection, "icon ghost ins-close");
  node.setAttribute("aria-label", "Close");
  return node;
}

function actionRow(...buttons) {
  const node = el("div", "ins-actions");
  node.append(...buttons.filter(Boolean));
  return node;
}

// Clamps a block to a few lines and offers a toggle only when it overflows.
function clamped(node, lines) {
  const wrap = el("div", "ins-clamp");
  node.classList.add("ins-clamped");
  node.style.setProperty("--lines", lines);
  const toggle = button("Show more", () => {
    const open = node.classList.toggle("ins-clamped");
    toggle.textContent = open ? "Show more" : "Show less";
  }, "ghost ins-more");
  toggle.hidden = true;
  wrap.append(node, toggle);
  requestAnimationFrame(() => (toggle.hidden = node.scrollHeight <= node.clientHeight + 1));
  return wrap;
}

function badgeFor(event) {
  const tone = eventTone(event);
  if (tone === "intervention") return el("span", "badge warn", interventionLabel(event));
  return null;
}

function overviewView(root, _selection, { state, run, branch, actions }) {
  const task = state.environment.task || state.environment.goal || "";
  const title = run.name;
  const head = el("header", "ins-overview-head");
  head.append(el("h2", "ins-title", title));
  root.append(head);
  if (branch.parent_id) {
    const added = branch.head - branch.fork_position;
    root.append(el("p", "ins-fork muted", `Forked at event ${formatNumber(branch.fork_position)} · ${formatNumber(added)} new event${added === 1 ? "" : "s"}`));
  }
  if (task && task !== title) {
    const text = el("p", "ins-task", task);
    if (task.length > LONG_TASK) {
      text.classList.add("ins-clamped");
      text.style.setProperty("--lines", 4);
      root.append(text, button("Read full task", actions.readTask, "ghost ins-more"));
    } else root.append(clamped(text, 4));
  }
  const goal = state.environment.goal;
  if (goal && goal !== task) root.append(section("Goal", el("p", "ins-goal", goal)));
  root.append(section("Agents", agentList(state, actions)));
  if (Object.keys(state.channels).length > 1) root.append(section("Topology", topologyGraph(state, actions.selectAgent)));
  root.append(actionRow(button("Add agent", actions.addAgent, "ghost"), button("Change goal", actions.editGoal, "ghost")));
}

function agentList(state, actions) {
  const list = el("div", "ins-agents");
  const agents = Object.values(state.agents);
  const max = Math.max(1, ...agents.map((agent) => state.activity[agent.id] || 0));
  if (!agents.length) list.append(el("p", "muted", "No agents yet"));
  for (const agent of agents) {
    const count = state.activity[agent.id] || 0;
    const row = button("", () => actions.selectAgent(agent.id), "ins-agent" + (agent.active ? "" : " ins-removed"));
    const label = el("span", "ins-agent-label");
    label.append(el("span", "ins-agent-name", agent.name), el("span", "ins-agent-model muted", agent.model || ""));
    row.append(avatar(agent), label, bar(count / max, color(agent.id, state.agents)), el("span", "ins-agent-count mono", formatNumber(count)));
    row.setAttribute("aria-label", `${agent.name}, ${count} messages${agent.active ? "" : ", removed"}`);
    list.append(row);
  }
  return list;
}

function bar(fraction, hue) {
  const track = el("span", "ins-bar");
  const fill = el("span");
  fill.style.width = `${fraction * 100}%`;
  fill.style.background = hue;
  track.append(fill);
  return track;
}

function eventAgentId(event, data) {
  return data.sender_id || data.agent_id || data.owner_id
    || (event.kind.startsWith("agent.") ? data.id : null) || event.agent_id;
}

function eventView(root, { event, detail }, { state, actions }) {
  const data = detail?.data || {};
  const agentId = eventAgentId(event, data);
  const agent = state.agents[agentId];
  const head = el("header", "ins-head");
  const who = el("div", "ins-who");
  const speaker = agent?.name || data.sender_name || speakerName(event, state.agents);
  const stamp = [event.stage_label, time(event.at), `#${event.position}`].filter(Boolean).join(" · ");
  who.append(el("span", "ins-name", speaker), el("span", "ins-stamp muted", stamp));
  head.append(avatar(agent || { name: speaker }), who, closeButton(actions));
  root.append(head);
  const badge = badgeFor(event);
  if (badge) root.append(badge);
  // Actions sit above the content: forking at this moment is the reason to select it.
  root.append(actionRow(
    button("Fork here", actions.fork, "primary"),
    agent && button("Change prompt…", () => actions.editAgent(agent), "ghost"),
    agent && button("Remove agent…", () => actions.removeAgent(agent), "ghost"),
    button("Change goal…", actions.editGoal, "ghost"),
  ));
  root.append(eventBody(event, data));
  const meta = eventMeta(event, data, agent);
  if (meta) root.append(meta);
  const sources = detail?.source?.delivered_sources;
  if (sources?.length) root.append(section("Received from", sourceList(sources, state)));
  const artifact = data.metadata?.model_output_artifact;
  if (artifact) root.append(disclosure("Recorded model response", button("Open response", () => actions.readArtifact(artifact))));
  if (detail?.source) root.append(disclosure("Source", el("pre", "metadata-box", JSON.stringify(detail.source, null, 2))));
}

function eventBody(event, data) {
  const node = el("div", "ins-body");
  if (event.kind.startsWith("tool.")) {
    node.append(el("p", "ins-tool", data.tool_name || event.label));
    if (data.arguments) node.append(el("pre", "metadata-box", JSON.stringify(data.arguments, null, 2)));
    if (Object.hasOwn(data, "result")) node.append(el("pre", "metadata-box", typeof data.result === "string" ? data.result : JSON.stringify(data.result, null, 2)));
    if (data.error) node.append(el("p", "ins-error", data.error));
    return node;
  }
  const text = data.content ?? data.goal ?? data.system_prompt ?? event.preview ?? "";
  const md = el("div");
  renderMarkdownInto(md, text).catch(failure);
  node.append(md);
  return node;
}

function eventMeta(event, data, agent) {
  const usage = data.metadata?.usage;
  const latency = data.metadata?.latency_seconds;
  const model = data.metadata?.model || event.model || agent?.model;
  const parts = [
    usage && `${formatNumber(usage.input_tokens ?? 0)} in · ${formatNumber(usage.output_tokens ?? 0)} out`,
    latency !== undefined && `${latency.toFixed(1)}s`,
    model,
  ].filter(Boolean);
  if (!parts.length) return null;
  const node = el("p", "ins-meta mono muted");
  node.textContent = parts.join(" · ");
  return node;
}

function sourceList(sources, state) {
  const list = el("div", "ins-sources");
  for (const id of sources) {
    const agent = state.agents[id];
    const chip = el("span", "ins-source");
    chip.append(avatar(agent || { name: id }), el("span", "", agent?.name || id));
    list.append(chip);
  }
  return list;
}

function disclosure(title, content) {
  const node = el("details", "ins-details");
  node.append(el("summary", "", title), content);
  return node;
}

function agentView(root, { agentId }, { state, actions }) {
  const agent = state.agents[agentId];
  if (!agent) {
    root.append(el("p", "empty", "Agent not present at this point"));
    return;
  }
  const head = el("header", "ins-head");
  const who = el("div", "ins-who");
  const name = el("span", "ins-name ins-name-lg");
  name.append(agent.name);
  if (!agent.active) name.append(el("span", "badge", "Removed"));
  who.append(name, el("span", "ins-stamp muted", agent.model || "Model not recorded"));
  head.append(avatar(agent, "avatar ins-avatar-lg"), who, closeButton(actions));
  root.append(head);
  const memories = state.memories.filter((memory) => memory.owner_id === agent.id);
  root.append(stats([["Messages", state.activity[agent.id] || 0], ["Memory slots", memories.length]]));
  const prompt = agent.system_prompt == null
    ? el("p", "muted", "Not recorded")
    : clamped(el("p", "ins-prompt", agent.system_prompt.trim()), 8);
  root.append(section("System prompt", prompt));
  if (memories.length) root.append(section("Memory", memoryList(memories, actions)));
  root.append(actionRow(
    button("Change prompt…", () => actions.editAgent(agent), "ghost"),
    button(agent.active ? "Remove agent…" : "Restore agent…", () => actions.removeAgent(agent), "ghost danger"),
  ));
}

function stats(pairs) {
  const node = el("dl", "ins-stats");
  for (const [label, value] of pairs) {
    const item = el("div", "ins-stat");
    item.append(el("dt", "muted", label), el("dd", "mono", formatNumber(value)));
    node.append(item);
  }
  return node;
}

function memoryList(memories, actions) {
  const list = el("div", "ins-memories");
  for (const memory of memories) {
    const row = el("div", "ins-memory");
    row.append(el("span", "ins-memory-text mono", memory.preview.replace(/\s+/g, " ")), button("Read", () => actions.readMemory(memory), "ghost"));
    list.append(row);
  }
  return list;
}

function analysisView(root, { record }, { state, actions }) {
  const output = record.output;
  const head = el("header", "ins-head");
  const who = el("div", "ins-who");
  who.append(el("span", "ins-name", "Activity"), el("span", "ins-stamp muted", `${record.plugin_id} v${record.plugin_version} · #${record.cursor}`));
  head.append(who, closeButton(actions));
  root.append(head, stats([
    ["Messages", output.messages],
    ["Tool calls", output.tool_calls],
    ["Memory slots", output.memory_items],
    ["Active agents", output.active_agents],
    ["Tool errors", output.tool_errors],
  ]));
  const entries = Object.entries(output.messages_by_agent).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, count]) => count));
  const list = el("div", "ins-agents");
  for (const [id, count] of entries) {
    const row = el("div", "ins-agent");
    const name = state.agents[id]?.name || id;
    row.append(avatar(state.agents[id] || { name }), el("span", "ins-agent-name", name), bar(count / max, color(id, state.agents)), el("span", "ins-agent-count mono", formatNumber(count)));
    list.append(row);
  }
  root.append(section("Participation", list));
  if (output.interpretation) root.append(el("p", "ins-note muted", output.interpretation));
}
