// "How far did this one message spread?" A message reaches every later message whose author
// read it (delivered_sources, or reply_to_id when a run records no deliveries), then every
// message that read one of those, and so on.
import { el, button, svg, color, avatar, stageOf, speakerName, formatNumber, tip } from "../ui.js";

const NODE_W = 176;
const NODE_H = 26;
const NODE_GAP = 8;
const COLUMN_GAP = 36;
const ROW_GAP = 12;
const HEAD_H = 30;
const PAD = 12;
const MAX_NODES = 400;
const RANK_SIZE = 5;

// Read edges between messages. Sources must precede their reader, so the graph is acyclic.
export function deliveryGraph(events) {
  const messages = events.filter((event) => event.kind === "message.created");
  const mode = messages.some((message) => Array.isArray(message.delivered_sources)) ? "delivered"
    : messages.some((message) => message.reply_to_id) ? "reply" : null;
  const indexByEntity = new Map();
  const parents = messages.map((message, index) => {
    const indices = sourcesOf(message, mode).map((id) => indexByEntity.get(id)).filter((source) => source !== undefined);
    if (message.entity_id) indexByEntity.set(message.entity_id, index);
    return [...new Set(indices)];
  });
  const children = messages.map(() => []);
  parents.forEach((sources, reader) => sources.forEach((source) => children[source].push(reader)));
  const indexById = new Map(messages.map((message, index) => [message.id, index]));
  return { mode, messages, parents, children, indexById };
}

function sourcesOf(message, mode) {
  if (mode === "delivered") return message.delivered_sources || [];
  if (mode === "reply" && message.reply_to_id) return [message.reply_to_id];
  return [];
}

// Number of messages at or before the cursor (messages are ordered by position).
export function visibleCount(graph, cursor) {
  let low = 0;
  let high = graph.messages.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (graph.messages[middle].position <= (cursor ?? Infinity)) low = middle + 1;
    else high = middle;
  }
  return low;
}

const agentKey = (message) => message.agent_id ?? "";

// Breadth-first spread from `root` over the first `limit` messages; depth is the shortest read chain.
export function cascade(graph, root, limit) {
  const depth = new Map([[root, 0]]);
  const queue = [root];
  for (let head = 0; head < queue.length; head++) {
    const node = queue[head];
    for (const reader of graph.children[node]) {
      if (reader >= limit) break;
      if (!depth.has(reader)) {
        depth.set(reader, depth.get(node) + 1);
        queue.push(reader);
      }
    }
  }
  const reached = queue.slice(1).sort((a, b) => a - b);
  const later = graph.messages.slice(root + 1, limit);
  const firstReach = new Map();
  for (const index of reached) {
    const key = agentKey(graph.messages[index]);
    if (!firstReach.has(key)) firstReach.set(key, index);
  }
  return {
    root, limit, depth, reached, firstReach,
    later: later.length,
    laterAgents: new Set(later.map(agentKey)).size,
    maxDepth: Math.max(0, ...depth.values()),
  };
}

function popcount(word) {
  let bits = word - ((word >>> 1) & 0x55555555);
  bits = (bits & 0x33333333) + ((bits >>> 2) & 0x33333333);
  return (((bits + (bits >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}

// Total downstream reach of every message within the first `limit`, via descendant bitsets built
// from the newest message backwards. A set only stores words from its own index on, since
// descendants always come later.
export function widestReach(graph, limit, size = RANK_SIZE) {
  const words = Math.ceil(limit / 32);
  const sets = new Array(limit).fill(null);
  const reach = new Uint32Array(limit);
  for (let index = limit - 1; index >= 0; index--) {
    const readers = graph.children[index].filter((reader) => reader < limit);
    if (!readers.length) continue;
    const start = index >> 5;
    const bits = new Uint32Array(words - start);
    for (const reader of readers) {
      bits[(reader >> 5) - start] |= 1 << (reader & 31);
      const descendants = sets[reader];
      if (!descendants) continue;
      const offset = descendants.start - start;
      for (let word = 0; word < descendants.bits.length; word++) bits[word + offset] |= descendants.bits[word];
    }
    sets[index] = { start, bits };
    reach[index] = bits.reduce((total, word) => total + popcount(word), 0);
  }
  return [...reach.keys()]
    .filter((index) => reach[index] > 0)
    .sort((a, b) => reach[b] - reach[a] || a - b)
    .slice(0, size)
    .map((index) => ({ index, reach: reach[index], later: limit - index - 1 }));
}

const percent = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);
const stageText = (message) => stageOf(message) || `#${formatNumber(message.position)}`;
const oneLine = (text) => (text || "").replace(/(?:\\n|\s)+/g, " ").trim();

function speakerAvatar(message, agents) {
  return avatar(agents?.[message.agent_id] || { name: speakerName(message, agents) }, "avatar br-avatar");
}

// Columns are stages when every shown message has one, otherwise read depth; rows are agents.
export function layout(graph, result) {
  const nodes = [result.root, ...result.reached].slice(0, MAX_NODES);
  const byStage = nodes.every((index) => stageOf(graph.messages[index]));
  const columnOf = (index) => (byStage ? stageOf(graph.messages[index]) : `Depth ${result.depth.get(index)}`);
  const columnRank = new Map();
  for (const index of byStage ? nodes : [...nodes].sort((a, b) => result.depth.get(a) - result.depth.get(b)))
    if (!columnRank.has(columnOf(index))) columnRank.set(columnOf(index), columnRank.size);
  const rowRank = new Map();
  for (const index of nodes) if (!rowRank.has(agentKey(graph.messages[index]))) rowRank.set(agentKey(graph.messages[index]), rowRank.size);

  const slots = new Map();
  const columnSlots = new Array(columnRank.size).fill(1);
  const cells = nodes.map((index) => {
    const column = columnRank.get(columnOf(index));
    const row = rowRank.get(agentKey(graph.messages[index]));
    const slot = slots.get(`${column}:${row}`) || 0;
    slots.set(`${column}:${row}`, slot + 1);
    columnSlots[column] = Math.max(columnSlots[column], slot + 1);
    return { index, column, row, slot };
  });
  const columnX = [];
  let x = PAD;
  for (const count of columnSlots) {
    columnX.push(x);
    x += count * (NODE_W + NODE_GAP) - NODE_GAP + COLUMN_GAP;
  }
  const positions = new Map(cells.map((cell) => [cell.index, {
    ...cell,
    x: columnX[cell.column] + cell.slot * (NODE_W + NODE_GAP),
    y: HEAD_H + PAD + cell.row * (NODE_H + ROW_GAP),
  }]));
  const columns = [...columnRank.keys()].map((label, rank) => ({ label, x: columnX[rank] }));
  return {
    positions, columns, byStage,
    width: x - COLUMN_GAP + PAD,
    height: HEAD_H + PAD * 2 + rowRank.size * (NODE_H + ROW_GAP) - ROW_GAP,
    hidden: result.reached.length + 1 - nodes.length,
  };
}

function edgePath(from, to) {
  if (to.x >= from.x + NODE_W) {
    const startX = from.x + NODE_W;
    const startY = from.y + NODE_H / 2;
    const endY = to.y + NODE_H / 2;
    const bend = Math.max(16, (to.x - startX) / 2);
    return `M${startX},${startY} C${startX + bend},${startY} ${to.x - bend},${endY} ${to.x},${endY}`;
  }
  const down = to.y > from.y;
  const startX = from.x + NODE_W / 2;
  const startY = down ? from.y + NODE_H : from.y;
  const endX = to.x + NODE_W / 2;
  const endY = down ? to.y : to.y + NODE_H;
  const bend = (endY - startY) / 2;
  return `M${startX},${startY} C${startX},${startY + bend} ${endX},${endY - bend} ${endX},${endY}`;
}

function renderEdges(graph, result, plan) {
  const layer = svg("svg", { class: "br-edges", width: plan.width, height: plan.height, "aria-hidden": "true" });
  for (const [index, to] of plan.positions) {
    for (const source of graph.parents[index]) {
      const from = plan.positions.get(source);
      if (from) layer.append(svg("path", { d: edgePath(from, to), class: source === result.root ? "br-edge br-edge-root" : "br-edge" }));
    }
  }
  return layer;
}

// Stage columns already name the stage, so a node there only names its speaker.
function nodeButton(message, place, result, byStage, context, actions) {
  const depth = result.depth.get(place.index);
  const speaker = speakerName(message, context.agents);
  const label = `${speaker} · ${stageText(message)}`;
  const node = button("", () => actions.select(message), depth === 0 ? "br-node br-node-root" : "br-node");
  node.style.left = `${place.x}px`;
  node.style.top = `${place.y}px`;
  node.style.setProperty("--agent", color(message.agent_id, context.agents));
  tip(node, `${label} · depth ${depth}\n${oneLine(message.preview)}`);
  node.setAttribute("aria-label", `${label}, depth ${depth}`);
  node.append(el("span", "br-dot"), el("span", "br-node-label", byStage ? speaker : label));
  return node;
}

function renderTree(graph, result, context, actions) {
  const plan = layout(graph, result);
  const plane = el("div", "br-plane");
  plane.style.width = `${plan.width}px`;
  plane.style.height = `${plan.height}px`;
  plane.append(renderEdges(graph, result, plan));
  for (const column of plan.columns) {
    const header = el("div", "br-column", column.label);
    header.style.left = `${column.x}px`;
    plane.append(header);
  }
  for (const [index, place] of plan.positions) plane.append(nodeButton(graph.messages[index], place, result, plan.byStage, context, actions));
  const canvas = el("div", "br-canvas");
  canvas.append(plane);
  return { canvas, hidden: plan.hidden };
}

function stat(value, label) {
  const node = el("span", "br-stat");
  node.append(el("strong", "", value), ` ${label}`);
  return node;
}

function firstReachList(graph, result, context, actions) {
  const list = el("div", "br-firsts");
  list.setAttribute("aria-label", "First reached");
  for (const index of result.firstReach.values()) {
    const message = graph.messages[index];
    const chip = button("", () => actions.select(message), "br-first");
    chip.style.setProperty("--agent", color(message.agent_id, context.agents));
    chip.append(speakerAvatar(message, context.agents), el("span", "", speakerName(message, context.agents)),
      el("span", "muted", stageText(message)));
    list.append(chip);
  }
  return list;
}

function cascadeView(graph, result, toEnd, context, actions) {
  const message = graph.messages[result.root];
  const title = el("div", "br-title");
  title.append(speakerAvatar(message, context.agents),
    el("strong", "", `${speakerName(message, context.agents)} · ${stageText(message)}`),
    el("span", "br-preview muted", oneLine(message.preview)));
  const reachedAgents = result.firstReach.size;
  const stats = el("div", "br-stats");
  stats.append(
    stat(`${formatNumber(result.reached.length)} of ${formatNumber(result.later)}`, `later messages (${percent(result.reached.length, result.later)}%)`),
    stat(`${reachedAgents} of ${result.laterAgents}`, "agents"),
    stat(String(result.maxDepth), result.maxDepth === 1 ? "hop deep" : "hops deep"),
    el("span", "muted", toEnd ? "to the end of the branch" : "up to the cursor"));
  const tree = renderTree(graph, result, context, actions);
  if (tree.hidden) stats.append(el("span", "muted", `${formatNumber(tree.hidden)} more not drawn`));
  const head = el("div", "br-head");
  head.append(title, stats);
  if (reachedAgents) head.append(firstReachList(graph, result, context, actions));
  return [head, tree.canvas];
}

function rankRow(graph, entry, top, context, actions) {
  const message = graph.messages[entry.index];
  const row = button("", () => actions.select(message), "br-rank");
  row.style.setProperty("--agent", color(message.agent_id, context.agents));
  row.style.setProperty("--share", `${(entry.reach / top) * 100}%`);
  const who = el("span", "br-rank-who");
  who.append(speakerAvatar(message, context.agents), el("strong", "", speakerName(message, context.agents)),
    el("span", "muted", stageText(message)));
  const reach = el("span", "br-rank-reach");
  reach.append(el("span", "br-meter"),
    el("span", "", `${formatNumber(entry.reach)} of ${formatNumber(entry.later)} · ${percent(entry.reach, entry.later)}%`));
  row.append(who, el("span", "br-preview muted", oneLine(message.preview)), reach);
  return row;
}

function rankingView(graph, limit, context, actions) {
  const ranking = widestReach(graph, limit);
  const head = el("div", "br-head");
  head.append(el("h3", "section-title", "Widest-reaching messages up to the cursor"));
  if (!ranking.length) return [head, el("p", "empty", "No message has been read yet.")];
  const list = el("div", "br-ranking");
  list.append(...ranking.map((entry) => rankRow(graph, entry, ranking[0].reach, context, actions)));
  return [head, list];
}

// The app moves the cursor onto a selected message; a cursor on the root shows the
// whole recorded spread, a later cursor cuts the spread off there.
function selectedRoot(graph, context) {
  const root = graph.indexById.get(context.selectedId);
  if (root === undefined) return null;
  const message = graph.messages[root];
  const cursor = context.cursor ?? Infinity;
  if (cursor < message.position) return null;
  const toEnd = cursor === message.position;
  return { root, toEnd, limit: toEnd ? graph.messages.length : visibleCount(graph, context.cursor) };
}

export const blastRadius = {
  id: "blast-radius",
  title: "Blast radius",
  about: {
    question: "How far did one message spread?",
    read: "Select a message: every later message that read it, directly or through others, appears by stage. With nothing selected, the widest-reaching messages are listed.",
    method: "Follows recorded delivery links forward from the message. When every agent reads every message, nearly everything reaches 100%.",
  },
  mount(root, actions) {
    const frame = el("div", "br");
    root.append(frame);
    let events = null;
    let graph = null;
    let rendered = null;

    const sameKey = (a, b) => a && a.graph === b.graph && a.root === b.root && a.limit === b.limit && a.agents === b.agents;

    return {
      update(context) {
        if (context.events !== events) {
          events = context.events;
          graph = deliveryGraph(events);
        }
        const selection = graph.mode ? selectedRoot(graph, context) : null;
        const limit = selection ? selection.limit : visibleCount(graph, context.cursor);
        const key = { graph, root: selection?.root ?? -1, limit, agents: context.agents };
        if (sameKey(rendered, key)) return;
        rendered = key;
        if (!graph.mode) frame.replaceChildren(el("p", "empty", "This run records no delivery data."));
        else if (selection) frame.replaceChildren(...cascadeView(graph, cascade(graph, selection.root, limit), selection.toEnd, context, actions));
        else frame.replaceChildren(...rankingView(graph, limit, context, actions));
      },
      destroy() {
        root.replaceChildren();
      },
    };
  },
};
