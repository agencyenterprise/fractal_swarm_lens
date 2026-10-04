import { el, svg, avatar, color, formatNumber, speakerName } from "./ui.js";

export const HUMAN = "__human";
// Room around the ring: half the largest node above, and its name and counts below.
const NODE_MIN = 30, NODE_MAX = 54, MARGIN_X = 80, MARGIN_TOP = NODE_MAX / 2 + 8, MARGIN_BOTTOM = NODE_MAX / 2 + 38;

const isMessage = (event) => event.kind === "message.created";
const authorOf = (event) => event.agent_id ?? HUMAN;
const plural = (count, word, many = `${word}s`) => `${formatNumber(count)} ${count === 1 ? word : many}`;

// The richest recorded link between messages wins: who read what, then who replied to what, then
// which agents post to which channels.
export function linkMode(events) {
  const messages = events.filter(isMessage);
  if (messages.some((event) => event.delivered_sources?.length)) return "reads";
  if (messages.some((event) => event.reply_to_id)) return "replies";
  return "channels";
}

function sourcesOf(event, mode) {
  if (mode === "reads") return event.delivered_sources || [];
  return event.reply_to_id ? [event.reply_to_id] : [];
}

export function messageIndex(events) {
  return new Map(events.filter((event) => isMessage(event) && event.entity_id).map((event) => [event.entity_id, event]));
}

// Each (read message, reading message) pair up to the cursor; `from` wrote it, `to` read it.
function reads(events, cursor, mode, index) {
  const pairs = [];
  for (const event of events) {
    if (!isMessage(event) || event.position > cursor) continue;
    for (const id of sourcesOf(event, mode)) {
      const source = index.get(id);
      if (source && source.position < event.position) pairs.push({ source, reader: event });
    }
  }
  return pairs;
}

// Author -> reader edges with read counts, plus per-agent sent, received and reach (times others read it).
export function influence(events, cursor, mode, index = messageIndex(events)) {
  const edges = new Map(), agents = new Map();
  const agent = (id) => {
    if (!agents.has(id)) agents.set(id, { sent: 0, received: 0, reach: 0, readFrom: new Map() });
    return agents.get(id);
  };
  for (const event of events) if (isMessage(event) && event.position <= cursor) agent(authorOf(event)).sent++;
  for (const { source, reader } of reads(events, cursor, mode, index)) {
    const from = authorOf(source), to = authorOf(reader);
    if (from === to) continue;
    const key = `${from}>${to}`;
    if (!edges.has(key)) edges.set(key, { key, from, to, weight: 0 });
    edges.get(key).weight++;
    const target = agent(to);
    target.received++;
    target.readFrom.set(from, (target.readFrom.get(from) || 0) + 1);
    agent(from).reach++;
  }
  return { edges: [...edges.values()], agents };
}

// What the selected message read (incoming) and which later messages read it directly (outgoing),
// including those after the cursor.
export function messageFlow(selected, events, mode, index = messageIndex(events)) {
  if (!selected || !isMessage(selected) || mode === "channels") return null;
  const incoming = sourcesOf(selected, mode).map((id) => index.get(id)).filter(Boolean);
  const outgoing = events.filter((event) => isMessage(event) && event.position > selected.position
    && sourcesOf(event, mode).includes(selected.entity_id));
  return { incoming, outgoing };
}

export function flowEdges(selected, flow, cursor) {
  const edges = new Map();
  const add = (from, to, direction, future) => {
    if (from === to) return;
    const key = `${from}>${to}`;
    const edge = edges.get(key) || { key, from, to, weight: 0, direction, future: true };
    edge.weight++;
    edge.future &&= future;
    edges.set(key, edge);
  };
  for (const source of flow.incoming) add(authorOf(source), authorOf(selected), "in", false);
  for (const reader of flow.outgoing) add(authorOf(selected), authorOf(reader), "out", reader.position > cursor);
  return [...edges.values()];
}

function channelEdges(state) {
  return state.edges.map((edge) => ({ key: `${edge.agent_id}>#${edge.channel_id}`, from: edge.agent_id,
    to: `#${edge.channel_id}`, weight: edge.count }));
}

export function ellipseLayout(ids, width, height, scale = 1) {
  const cx = width / 2, cy = (MARGIN_TOP + height - MARGIN_BOTTOM) / 2;
  const ry = Math.max(0, ((height - MARGIN_TOP - MARGIN_BOTTOM) / 2) * scale);
  const rx = Math.min(Math.max(0, (width / 2 - MARGIN_X) * scale), ry * 1.8);
  if (ids.length === 1) return new Map([[ids[0], { x: cx, y: cy }]]);
  return new Map(ids.map((id, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / ids.length;
    return [id, { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) }];
  }));
}

// Reach decides size on a square-root scale relative to the busiest agent, so one dominant voice stands out
// without the rest shrinking to dots.
function nodeSize(reach, maxReach) {
  return Math.round(NODE_MIN + (NODE_MAX - NODE_MIN) * Math.sqrt(reach / Math.max(1, maxReach)));
}

function towards(from, to, distance) {
  const dx = to.x - from.x, dy = to.y - from.y, length = Math.hypot(dx, dy) || 1;
  return { x: from.x + (dx / length) * distance, y: from.y + (dy / length) * distance };
}

// A gentle curve keeps A->B and B->A apart; both ends stop at the node rims.
function edgeShape(a, b, startGap, endGap, width) {
  const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const bend = Math.min(30, length * 0.14);
  const control = { x: (a.x + b.x) / 2 - ((b.y - a.y) / length) * bend, y: (a.y + b.y) / 2 + ((b.x - a.x) / length) * bend };
  const start = towards(a, control, startGap), tip = towards(b, control, endGap);
  const size = 5 + width * 1.5;
  const base = towards(tip, control, size);
  const nx = (tip.y - base.y) * 0.55, ny = (base.x - tip.x) * 0.55;
  return {
    line: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${base.x} ${base.y}`,
    arrow: `M ${tip.x} ${tip.y} L ${base.x + nx} ${base.y + ny} L ${base.x - nx} ${base.y - ny} Z`,
  };
}

class InfluenceGraph {
  constructor(root, actions) {
    this.root = root;
    this.actions = actions;
    this.nodes = new Map();
    root.classList.add("viz-graph");
    this.stage = el("div", "viz-graph-stage");
    this.edgeLayer = svg("svg", { class: "viz-graph-edges" });
    this.nodeLayer = el("div", "viz-graph-nodes");
    this.empty = el("p", "empty viz-graph-empty", "No agents at this event");
    this.footer = el("p", "viz-graph-footer");
    this.stage.append(this.edgeLayer, this.nodeLayer, this.empty);
    root.append(this.stage, this.footer);
    this.stage.addEventListener("contextmenu", (event) => this.onContextMenu(event));
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(this.stage);
  }

  update(context) {
    if (context.events !== this.events || context.branch !== this.branch) {
      this.events = context.events;
      this.branch = context.branch;
      this.mode = linkMode(context.events);
      this.index = messageIndex(context.events);
    }
    this.context = context;
    this.render();
  }

  destroy() {
    this.resizeObserver.disconnect();
    this.root.classList.remove("viz-graph");
    this.root.replaceChildren();
  }

  selectedEvent() {
    const { selectedId, events } = this.context;
    return selectedId ? events.find((event) => event.id === selectedId) : null;
  }

  model() {
    const { events, cursor, state } = this.context;
    const stats = influence(events, cursor, this.mode, this.index);
    const edges = this.mode === "channels" ? channelEdges(state) : stats.edges;
    const agentIds = Object.keys(state.agents);
    if (edges.some((edge) => edge.from === HUMAN || edge.to === HUMAN)) agentIds.push(HUMAN);
    const channelIds = this.mode === "channels" ? Object.keys(state.channels).map((id) => `#${id}`) : [];
    const selected = this.selectedEvent();
    const flow = messageFlow(selected, events, this.mode, this.index);
    return { stats, edges, agentIds, channelIds, selected, flow,
      highlight: flow ? flowEdges(selected, flow, cursor) : this.channelHighlight(selected) };
  }

  channelHighlight(selected) {
    if (this.mode !== "channels" || !selected?.channel_id || !isMessage(selected)) return null;
    return [{ key: `${authorOf(selected)}>#${selected.channel_id}`, from: authorOf(selected), to: `#${selected.channel_id}`,
      weight: 1, direction: "out", future: false }];
  }

  render() {
    if (!this.context) return;
    const width = this.stage.clientWidth, height = this.stage.clientHeight;
    if (!width || !height) return;
    const model = this.model();
    const empty = !model.agentIds.length;
    this.empty.hidden = !empty;
    this.edgeLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.edgeLayer.setAttribute("width", width);
    this.edgeLayer.setAttribute("height", height);
    if (empty) {
      this.edgeLayer.replaceChildren();
      this.syncNodes([], new Map(), model);
      this.footer.textContent = "";
      return;
    }
    const points = ellipseLayout(model.agentIds, width, height);
    for (const [id, point] of ellipseLayout(model.channelIds, width, height, 0.35)) points.set(id, point);
    const maxReach = Math.max(1, ...[...model.stats.agents.values()].map((agent) => agent.reach));
    const sizes = new Map(model.agentIds.map((id) => [id, nodeSize(model.stats.agents.get(id)?.reach || 0, maxReach)]));
    for (const id of model.channelIds) sizes.set(id, 22);
    this.drawEdges(model, points, sizes);
    this.syncNodes([...model.agentIds, ...model.channelIds], points, model, sizes);
    this.renderFooter(model);
  }

  drawEdges(model, points, sizes) {
    const highlighted = new Map((model.highlight || []).map((edge) => [edge.key, edge]));
    const edges = [...model.edges, ...[...highlighted.values()].filter((edge) => !model.edges.some((base) => base.key === edge.key))];
    const maxWeight = Math.max(1, ...model.edges.map((edge) => edge.weight));
    const items = [];
    for (const edge of edges) {
      const a = points.get(edge.from), b = points.get(edge.to);
      if (!a || !b) continue;
      const mark = highlighted.get(edge.key);
      const width = 1 + (3 * Math.min(edge.weight, maxWeight)) / maxWeight;
      const shape = edgeShape(a, b, sizes.get(edge.from) / 2 + 3, sizes.get(edge.to) / 2 + 3, width);
      const state = mark ? ` is-${mark.direction}${mark.future ? " is-future" : ""}` : model.highlight ? " is-dim" : "";
      const group = svg("g", { class: `viz-graph-edge${state}` });
      group.append(svg("title", {}, this.edgeLabel(edge, model)),
        svg("path", { class: "viz-graph-line", d: shape.line, "stroke-width": width }),
        svg("path", { class: "viz-graph-arrow", d: shape.arrow }));
      items.push(group);
    }
    this.edgeLayer.replaceChildren(...items);
  }

  name(id) {
    if (id === HUMAN) return "Human";
    if (id.startsWith("#")) return this.context.state.channels[id.slice(1)]?.name || id.slice(1);
    return this.context.state.agents[id]?.name || id;
  }

  edgeLabel(edge, model) {
    if (this.mode === "channels") return `${this.name(edge.from)} posted ${plural(edge.weight, "message")} to ${this.name(edge.to)}`;
    const count = model.edges.find((base) => base.key === edge.key)?.weight ?? 0;
    const verb = this.mode === "reads" ? "read" : "replied to";
    return `${this.name(edge.to)} ${verb} ${plural(count, "message")} from ${this.name(edge.from)}`;
  }

  nodeTitle(id, model) {
    const stats = model.stats.agents.get(id) || { sent: 0, received: 0, reach: 0, readFrom: new Map() };
    const lines = [this.name(id), `Sent ${formatNumber(stats.sent)} · read ${formatNumber(stats.received)}`];
    if (this.mode === "channels") return lines.join("\n");
    const sources = [...stats.readFrom].sort((a, b) => b[1] - a[1]).map(([from, count]) => `${formatNumber(count)} from ${this.name(from)}`);
    if (sources.length) lines.push(`Read ${sources.join(", ")}`);
    lines.push(`Read by others ${plural(stats.reach, "time")}`);
    return lines.join("\n");
  }

  syncNodes(ids, points, model, sizes) {
    const keep = new Set(ids);
    for (const [id, node] of this.nodes) if (!keep.has(id)) { node.remove(); this.nodes.delete(id); }
    const involved = model.highlight && new Set(model.highlight.flatMap((edge) => [edge.from, edge.to]));
    for (const id of ids) {
      const node = this.nodes.get(id) || this.createNode(id);
      const point = points.get(id), size = sizes.get(id);
      node.style.left = `${point.x}px`;
      node.style.top = `${point.y}px`;
      node.style.setProperty("--size", `${size}px`);
      node.title = this.nodeTitle(id, model);
      node.classList.toggle("is-dim", !!involved && !involved.has(id));
      node.classList.toggle("is-author", !!model.selected && authorOf(model.selected) === id);
      node.classList.toggle("is-inactive", this.context.state.agents[id]?.active === false);
      if (!id.startsWith("#")) this.paintCounts(node, model.stats.agents.get(id));
    }
  }

  createNode(id) {
    const channel = id.startsWith("#");
    const node = el(channel || id === HUMAN ? "div" : "button", `viz-graph-node${channel ? " is-channel" : ""}`);
    const face = channel ? el("span", "avatar viz-graph-avatar", "#") : avatar(this.context.state.agents[id] || { name: "Human" }, "avatar viz-graph-avatar");
    face.style.setProperty("--c", channel || id === HUMAN ? "var(--border-strong)" : color(id, this.context.state.agents));
    node.append(face, el("span", "viz-graph-name", this.name(id)));
    if (!channel) node.append(el("span", "viz-graph-counts mono"));
    if (node.tagName === "BUTTON") {
      node.type = "button";
      node.addEventListener("click", () => this.actions.selectAgent(id));
    }
    this.nodes.set(id, node);
    this.nodeLayer.append(node);
    return node;
  }

  paintCounts(node, stats = { sent: 0, received: 0 }) {
    const counts = node.querySelector(".viz-graph-counts");
    counts.textContent = this.mode === "channels" ? formatNumber(stats.sent) : `${formatNumber(stats.sent)} ↑ ${formatNumber(stats.received)} ↓`;
    counts.setAttribute("aria-label", `${stats.sent} sent, ${stats.received} read`);
  }

  renderFooter(model) {
    this.footer.classList.toggle("is-caption", !!model.highlight);
    if (model.flow) {
      const speaker = speakerName(model.selected, this.context.state.agents);
      const verb = this.mode === "reads" ? "read" : "replied to";
      this.footer.textContent = `${speaker}'s message ${verb} ${plural(model.flow.incoming.length, "message")} · `
        + `${plural(model.flow.outgoing.length, "later message")} ${verb} it`;
    } else if (model.highlight) {
      this.footer.textContent = `Posted in ${this.name(model.highlight[0].to)}`;
    } else {
      const total = model.edges.reduce((sum, edge) => sum + edge.weight, 0);
      this.footer.textContent = {
        reads: `Author → reader · ${plural(total, "read")} · ↑ sent ↓ read · size: times read by others`,
        replies: `Author → replier · ${plural(total, "reply", "replies")} · ↑ sent ↓ replied to`,
        channels: "Agent → channel",
      }[this.mode];
    }
  }

  onContextMenu(event) {
    event.preventDefault();
    this.actions.contextMenu(this.selectedEvent(), this.context.cursor, { x: event.clientX, y: event.clientY });
  }
}

export const influenceVisualization = {
  id: "influence",
  title: "Influence",
  mount: (root, actions) => new InfluenceGraph(root, actions),
};
