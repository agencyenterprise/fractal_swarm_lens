import { el, button, avatar, color, eventTone, speakerName, stageOf, time, formatNumber, failure, tip, recordedUsage } from "./ui.js";
import { forkTips } from "./branch.js";
import { renderMarkdownInto } from "./markdown.js";
import { findingsForEvent, eventSpan } from "./findings.js";
import { interventionLabel } from "./transcript.js";

const LONG_TASK = 400;

export function renderInspector(root, selection, ctx) {
  const view = {
    overview: overviewView,
    agent: agentView,
    event: eventView,
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
  return [selection.type, selection.agentId, selection.event?.id].join(":");
}

function section(title, ...children) {
  const node = el("section", "ins-section");
  node.append(el("h3", "section-title", title), ...children);
  return node;
}

const tipped = (hint, ...args) => tip(button(...args), hint);

function closeButton(actions) {
  const node = tipped("Close (Esc)", "×", actions.clearSelection, "icon ghost ins-close");
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
  if (tone === "intervention") return tip(el("span", "badge warn", interventionLabel(event)), "Change made when forking");
  return null;
}

function overviewView(root, _selection, { state, events, run, branch, comments, actions }) {
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
  const threads = comments.overviewSection(events, state.agents);
  if (threads) root.append(threads);
  root.append(actionRow(tipped(forkTips.here, "Fork here", actions.fork, "primary"),
    tipped(forkTips.agent, "Fork with new agent…", actions.addAgent, "ghost"),
    tipped(forkTips.goal, "Fork with new goal…", actions.editGoal, "ghost")));
}

function agentList(state, actions) {
  const list = el("div", "ins-agents");
  const agents = Object.values(state.agents);
  const max = Math.max(1, ...agents.map((agent) => state.activity[agent.id] || 0));
  if (!agents.length) list.append(el("p", "muted", "No agents yet"));
  for (const agent of agents) {
    const count = state.activity[agent.id] || 0;
    const row = tipped("Show this agent and only its events", "", () => actions.selectAgent(agent.id), "ins-agent" + (agent.active ? "" : " ins-removed"));
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

function eventView(root, { event, detail }, { state, events, comments, findings, interventions, actions }) {
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
    tipped(forkTips.here, "Fork here", actions.fork, "primary"),
    agent && tipped(forkTips.prompt(agent.name), "Fork with new prompt…", () => actions.editAgent(agent), "ghost"),
    agent && removeButton(agent, actions, "ghost"),
    tipped(forkTips.goal, "Fork with new goal…", actions.editGoal, "ghost"),
    ...interventions.map((plugin) => tipped(plugin.description, `Fork with ${plugin.title}…`, () => actions.forkWithPlugin(plugin, event), "ghost")),
    tipped("Start a comment thread on this event (C)", "Comment", () => actions.comment(event), "ghost"),
  ));
  const threads = comments.eventSection(event);
  if (threads) root.append(threads);
  const found = findingsSection(findingsForEvent(findings, event), state);
  if (found) root.append(found);
  root.append(eventBody(event, data));
  const meta = eventMeta(event, data, agent);
  if (meta) root.append(meta);
  const sources = detail?.source?.delivered_sources;
  if (sources?.length) {
    const read = section("Read before answering", sourceList(sources, events, state, actions));
    tip(read.firstChild, "Messages delivered to this agent before it wrote this one");
    root.append(read);
  }
  const artifact = data.metadata?.model_output_artifact;
  if (artifact) root.append(disclosure("Recorded model response",
    tipped("The raw provider response saved for this message", "Open response", () => actions.readArtifact(artifact))));
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
  const usage = recordedUsage(data.metadata?.usage);
  const latency = data.metadata?.latency_seconds;
  const model = data.metadata?.model || event.model || agent?.model;
  const parts = [
    usage && [`${formatNumber(usage.input_tokens ?? 0)} in · ${formatNumber(usage.output_tokens ?? 0)} out`, "Input and output tokens of this model call"],
    latency !== undefined && [`${latency.toFixed(1)}s`, "Time the model took to respond"],
    model && [model, "Model that wrote this"],
  ].filter(Boolean);
  if (!parts.length) return null;
  const node = el("p", "ins-meta mono muted");
  parts.forEach(([text, hint], index) => {
    if (index) node.append(" · ");
    node.append(tip(el("span", "", text), hint));
  });
  return node;
}

// delivered_sources holds message ids; each chip names the message's author and stage and opens it.
function sourceList(sources, events, state, actions) {
  const messages = new Map(events.filter((item) => item.kind === "message.created").map((item) => [item.entity_id, item]));
  const list = el("div", "ins-sources");
  for (const id of sources) {
    const message = messages.get(id);
    const agent = message && state.agents[message.agent_id];
    const name = message ? speakerName(message, state.agents) : id;
    const label = [name, message && stageOf(message)].filter(Boolean).join(" · ");
    const chip = button("", () => actions.selectEvent(message), "ins-source");
    chip.disabled = !message;
    chip.append(avatar(agent || { name }), el("span", "", label));
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
    tipped(forkTips.prompt(agent.name), "Fork with new prompt…", () => actions.editAgent(agent), "ghost"),
    removeButton(agent, actions, "ghost danger"),
  ));
}

function removeButton(agent, actions, className) {
  return agent.active
    ? tipped(forkTips.remove(agent.name), "Fork without agent…", () => actions.removeAgent(agent), className)
    : tipped(forkTips.restore(agent.name), "Fork restoring agent…", () => actions.removeAgent(agent), className);
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

// Plugin findings at the selected event; the section is left out when there are none.
function findingsSection({ annotations, metrics }, state) {
  if (!annotations.length && !metrics.length) return null;
  const node = section("Findings");
  if (annotations.length) {
    const list = el("div", "ins-findings");
    for (const annotation of annotations) {
      const item = el("div", "ins-finding");
      if (annotation.agent_id) item.style.setProperty("--c", color(annotation.agent_id, state.agents));
      const source = [annotation.plugin, eventSpan(annotation).toLowerCase(),
        annotation.score === null || annotation.score === undefined ? "" : `score ${formatNumber(annotation.score)}`,
        annotation.agent_id && (state.agents[annotation.agent_id]?.name || annotation.agent_id)];
      item.append(el("span", "ins-finding-label", annotation.label), el("span", "ins-finding-source muted", source.filter(Boolean).join(" · ")));
      list.append(item);
    }
    node.append(list);
  }
  if (metrics.length) {
    const list = el("dl", "ins-metrics");
    for (const metric of metrics) {
      const agent = metric.agent_id === null ? "" : ` (${state.agents[metric.agent_id]?.name || metric.agent_id})`;
      list.append(el("dt", "", `${metric.plugin} · ${metric.name}${agent}`), el("dd", "mono", formatNumber(metric.value)));
    }
    node.append(list);
  }
  return node;
}
