// Echo: how much of each message repeats what its author had just read.
import { el, button, svg, color, avatar, stageOf, formatNumber, tip } from "../ui.js";

// Five words is long enough that shared phrasing is copying rather than common idiom
// ("the answer is", "step by step"), and short enough to catch paraphrase with light edits.
const SHINGLE_WORDS = 5;
const JUMP_POINTS = 0.3;
const MAX_JUMPS = 5;
const MAX_IN_FLIGHT = 6;
const WINDOW_THRESHOLD = 2000;
const WINDOW_SIZE = 400;
const CACHE_LIMIT = 8000;
const REFRESH_MS = 120;
const MARGIN = { top: 12, right: 14, bottom: 26, left: 40 };

// Shingles survive view switches, so returning to Echo does not re-read the run.
const shingleCache = new Map();

export function shingles(text) {
  const words = String(text ?? "").toLowerCase().replace(/[^\p{L}\p{N}\s]+/gu, " ").split(/\s+/).filter(Boolean);
  const result = new Set();
  for (let index = 0; index + SHINGLE_WORDS <= words.length; index++)
    result.add(words.slice(index, index + SHINGLE_WORDS).join(" "));
  return result;
}

function overlapShare(own, other) {
  if (!own.size || !other) return 0;
  let shared = 0;
  for (const shingle of own) if (other.has(shingle)) shared++;
  return shared / own.size;
}

function unionShare(own, others) {
  let shared = 0;
  for (const shingle of own) if (others.some((set) => set.has(shingle))) shared++;
  return shared / own.size;
}

export const isMessage = (event) => event.kind === "message.created";

// Who each message could have copied: what it was delivered, or, without delivery records,
// its author's previous message and the latest message of every other speaker.
export function messageLinks(messages) {
  const delivered = messages.some((message) => Array.isArray(message.delivered_sources));
  const byEntity = new Map();
  const latestBySpeaker = new Map();
  const links = messages.map((message) => {
    const previousOwn = latestBySpeaker.get(message.agent_id) ?? null;
    const sources = delivered
      ? (message.delivered_sources ?? []).map((id) => byEntity.get(id)).filter(Boolean)
      : [...latestBySpeaker.values()];
    if (message.entity_id != null) byEntity.set(message.entity_id, message);
    latestBySpeaker.set(message.agent_id, message);
    return { message, sources, previousOwn };
  });
  return { delivered, links };
}

// `shinglesOf(event)` returns a Set, or undefined while the full text is not loaded.
export function scoreLink({ message, sources, previousOwn }, shinglesOf) {
  const own = shinglesOf(message);
  const sourceSets = sources.map(shinglesOf);
  const previousSet = previousOwn && shinglesOf(previousOwn);
  if (!own || sourceSets.some((set) => !set) || (previousOwn && !previousSet)) return { message, pending: true };
  if (!own.size) return { message, pending: false, echo: null, top: null, selfEcho: null };
  const shares = sources.map((source, index) => ({ source, share: overlapShare(own, sourceSets[index]) }));
  const top = shares.reduce((best, entry) => (entry.share > (best?.share ?? 0) ? entry : best), null);
  return {
    message, pending: false,
    echo: sources.length ? unionShare(own, sourceSets) : null,
    top,
    selfEcho: previousOwn ? overlapShare(own, previousSet) : null,
  };
}

// A jump is a rise of at least 30 points over the same agent's previous scored message.
export function findJumps(scores, limit = MAX_JUMPS) {
  const lastEcho = new Map();
  const jumps = [];
  for (const score of scores) {
    if (score.echo == null) continue;
    const agentId = score.message.agent_id;
    const before = lastEcho.get(agentId);
    if (before && score.echo - before.echo >= JUMP_POINTS - 1e-9) jumps.push({ score, from: before.echo, rise: score.echo - before.echo });
    lastEcho.set(agentId, score);
  }
  return jumps.sort((a, b) => b.rise - a.rise).slice(0, limit);
}

// One x slot per stage in order of first appearance; messages without a stage get their own slot.
export function stageSlots(messages) {
  const slotByStage = new Map();
  const slots = [];
  const slotIndex = messages.map((message) => {
    const stage = stageOf(message);
    if (stage && slotByStage.has(stage)) return slotByStage.get(stage);
    slots.push(stage ?? `#${formatNumber(message.position)}`);
    if (stage) slotByStage.set(stage, slots.length - 1);
    return slots.length - 1;
  });
  return { slots, slotIndex };
}

const percent = (value) => (value == null ? "—" : `${Math.round(value * 100)}%`);

function cachedShingles(event) {
  return shingleCache.get(event.id);
}

function remember(event, text) {
  shingleCache.set(event.id, shingles(text));
  if (shingleCache.size > CACHE_LIMIT) shingleCache.delete(shingleCache.keys().next().value);
}

// Fetches full texts with at most six requests in flight; a new request list replaces the queue.
class TextLoader {
  constructor(loadDetail, onChange) {
    this.loadDetail = loadDetail;
    this.onChange = onChange;
    this.queue = [];
    this.inFlight = 0;
    this.failed = new Map();
    this.stopped = false;
  }

  request(events) {
    const missing = new Map();
    for (const event of events)
      if (!shingleCache.has(event.id) && !this.failed.has(event.id)) missing.set(event.id, event);
    this.queue = [...missing.values()];
    this.pump();
  }

  pump() {
    while (!this.stopped && this.inFlight < MAX_IN_FLIGHT && this.queue.length) this.fetch(this.queue.shift());
  }

  async fetch(event) {
    this.inFlight++;
    try {
      const detail = await this.loadDetail(event);
      if (typeof detail?.data?.content !== "string") throw new Error(`Message ${event.id} has no text content.`);
      remember(event, detail.data.content);
    } catch (error) {
      console.error(error);
      this.failed.set(event.id, error);
    } finally {
      this.inFlight--;
      if (!this.stopped) {
        this.pump();
        this.onChange();
      }
    }
  }

  stop() {
    this.stopped = true;
    this.queue = [];
  }
}

function windowRange(count, centerIndex) {
  if (count <= WINDOW_THRESHOLD) return [0, count];
  const start = Math.max(0, Math.min(count - WINDOW_SIZE, centerIndex - WINDOW_SIZE / 2));
  return [start, start + WINDOW_SIZE];
}

function lastIndexAtOrBefore(messages, cursor) {
  let low = 0;
  let high = messages.length - 1;
  let found = 0;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (messages[middle].position <= cursor) { found = middle; low = middle + 1; } else high = middle - 1;
  }
  return found;
}

function agentNamesFrom(events) {
  const names = new Map();
  for (const event of events) if (event.kind === "agent.added" || event.kind === "agent.updated") if (event.agent_name) names.set(event.agent_id, event.agent_name);
  return names;
}

class EchoView {
  constructor(root, actions, toolbar) {
    this.root = root;
    this.actions = actions;
    this.toolbar = toolbar;
    this.showSelf = false;
    this.derivedFor = null;
    this.range = null;
    this.refreshTimer = null;
    this.loader = new TextLoader(actions.loadDetail, () => this.scheduleRefresh());
    this.build();
    this.resizeObserver = new ResizeObserver(() => this.context && this.renderChart());
    this.resizeObserver.observe(this.chartHost);
  }

  build() {
    this.selfToggle = button("Self-echo", () => this.toggleSelf(), "ghost echo-self-toggle");
    this.selfToggle.setAttribute("aria-pressed", "false");
    tip(this.selfToggle, "Also show how much each message repeats its author's previous message");
    this.toolbar?.append(this.selfToggle);
    this.status = el("div", "echo-status muted");
    this.legend = el("div", "echo-legend");
    this.chartHost = el("div", "echo-chart");
    this.detail = el("section", "echo-detail");
    this.jumpList = el("section", "echo-jumps");
    const main = el("div", "echo-main");
    main.append(this.legend, this.chartHost);
    const side = el("aside", "echo-side");
    side.append(this.detail, this.jumpList);
    const body = el("div", "echo-body");
    body.append(main, side);
    this.root.classList.add("echo");
    this.root.replaceChildren(this.status, body);
  }

  toggleSelf() {
    this.showSelf = !this.showSelf;
    this.selfToggle.setAttribute("aria-pressed", String(this.showSelf));
    if (this.context) this.renderChart();
  }

  update(context) {
    const dataChanged = context.events !== this.context?.events;
    this.context = context;
    if (dataChanged) this.derive(context.events);
    const range = windowRange(this.messages.length, lastIndexAtOrBefore(this.messages, context.cursor));
    if (dataChanged || this.needsNewWindow(range)) {
      this.range = range;
      this.requestTexts();
      this.score();
      this.renderAll();
    } else this.paint();
  }

  derive(events) {
    this.messages = events.filter(isMessage);
    this.linkSet = messageLinks(this.messages);
    this.names = agentNamesFrom(events);
  }

  // Recentre only once the cursor leaves the middle half, so stepping does not re-read texts.
  needsNewWindow([start]) {
    const [currentStart, currentEnd] = this.range;
    if (start === currentStart) return false;
    const index = lastIndexAtOrBefore(this.messages, this.context.cursor);
    const quarter = (currentEnd - currentStart) / 4;
    return index < currentStart + quarter || index > currentEnd - quarter;
  }

  windowLinks() {
    return this.linkSet.links.slice(...this.range);
  }

  requestTexts() {
    const needed = [];
    for (const { message, sources, previousOwn } of this.windowLinks()) needed.push(message, ...sources, ...(previousOwn ? [previousOwn] : []));
    this.loader.request(needed);
  }

  scheduleRefresh() {
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.score();
      this.renderAll();
    }, REFRESH_MS);
  }

  // Messages without an agent (a task prompt, a human turn) are sources only, never plotted.
  score() {
    const links = this.windowLinks().filter((link) => link.message.agent_id != null);
    this.scores = links.map((link) => scoreLink(link, cachedShingles));
    this.scoreById = new Map(this.scores.map((score) => [score.message.id, score]));
    this.slotData = stageSlots(links.map((link) => link.message));
    this.jumps = findJumps(this.scores);
  }

  name(agentId) {
    return this.context.agents?.[agentId]?.name ?? this.names.get(agentId) ?? agentId ?? "Human";
  }

  renderAll() {
    this.renderStatus();
    this.renderLegend();
    this.renderChart();
    this.renderJumps();
    this.renderDetail();
  }

  paint() {
    this.paintPoints();
    this.renderDetail();
    this.renderJumps();
  }

  renderStatus() {
    const lines = [];
    const total = this.messages.length;
    const [start, end] = this.range;
    if (end - start < total) lines.push(`Showing ${formatNumber(end - start)} of ${formatNumber(total)} messages around the cursor.`);
    const read = this.scores.filter((score) => !score.pending).length;
    if (read < this.scores.length) lines.push(`Reading messages ${formatNumber(read)} of ${formatNumber(this.scores.length)}…`);
    if (this.loader.failed.size) lines.push(`${formatNumber(this.loader.failed.size)} messages could not be loaded: ${this.loader.failed.values().next().value.message}`);
    if (total && !this.linkSet.delivered) lines.push("No delivery records: compared with each agent's previous message and the latest message of every other agent.");
    this.status.replaceChildren(...lines.map((line) => el("div", "", line)));
    this.status.hidden = !lines.length;
  }

  agentIds() {
    return [...new Set(this.scores.map((score) => score.message.agent_id))];
  }

  renderLegend() {
    this.legend.replaceChildren(...this.agentIds().map((agentId) => {
      const item = button("", () => this.actions.selectAgent(agentId), "ghost echo-legend-item");
      item.style.setProperty("--agent", color(agentId, this.context.agents));
      item.append(el("span", "echo-swatch"), el("span", "", this.name(agentId)));
      return item;
    }));
  }

  geometry() {
    const width = Math.max(240, this.chartHost.clientWidth || 640);
    const height = Math.max(120, this.chartHost.clientHeight || 220);
    const slotCount = Math.max(1, this.slotData.slots.length);
    const plotWidth = width - MARGIN.left - MARGIN.right;
    const plotHeight = height - MARGIN.top - MARGIN.bottom;
    const slotWidth = plotWidth / slotCount;
    return {
      width, height, slotWidth,
      x: (slot, offset = 0.5) => MARGIN.left + (slot + offset) * slotWidth,
      y: (value) => MARGIN.top + (1 - value) * plotHeight,
    };
  }

  // Several messages from one agent in the same stage are spread across the slot.
  pointOffsets() {
    const counts = new Map();
    const keys = this.scores.map((score, index) => {
      const key = `${this.slotData.slotIndex[index]}|${score.message.agent_id}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
      return key;
    });
    const seen = new Map();
    return keys.map((key) => {
      const order = seen.get(key) ?? 0;
      seen.set(key, order + 1);
      return (order + 0.5) / counts.get(key);
    });
  }

  renderChart() {
    if (!this.messages.length) {
      this.chartHost.replaceChildren(el("div", "empty", "No messages yet."));
      this.points = [];
      return;
    }
    const geometry = this.geometry();
    const chart = svg("svg", { class: "echo-svg", width: geometry.width, height: geometry.height, role: "img", "aria-label": "Echo per message by stage" });
    chart.append(this.axes(geometry));
    const offsets = this.pointOffsets();
    const placed = this.scores.map((score, index) => ({ score, x: geometry.x(this.slotData.slotIndex[index], offsets[index]) }));
    if (this.showSelf) chart.append(this.lines(placed, geometry, "selfEcho", "echo-self-line"));
    chart.append(this.lines(placed, geometry, "echo", "echo-line"));
    const pointLayer = svg("g", { class: "echo-points" });
    this.points = placed.filter(({ score }) => score.echo != null).map((entry) => this.point(entry, geometry));
    pointLayer.append(...this.points.map((point) => point.node));
    chart.append(pointLayer);
    this.chartHost.replaceChildren(chart);
    this.paintPoints();
  }

  axes(geometry) {
    const group = svg("g", { class: "echo-axes" });
    for (const value of [0, 0.5, 1]) {
      const y = geometry.y(value);
      group.append(svg("line", { class: "echo-grid", x1: MARGIN.left, x2: geometry.width - MARGIN.right, y1: y, y2: y }),
        svg("text", { class: "echo-tick", x: MARGIN.left - 6, y: y + 4, "text-anchor": "end" }, percent(value)));
    }
    const { slots } = this.slotData;
    const every = Math.max(1, Math.ceil(slots.length / Math.max(1, Math.floor((geometry.width - MARGIN.left) / 110))));
    slots.forEach((label, slot) => {
      if (slot % every) return;
      const atEnd = geometry.x(slot) + label.length * 3.5 > geometry.width - MARGIN.right;
      const text = svg("text", { class: "echo-tick", x: atEnd ? geometry.width - MARGIN.right : geometry.x(slot), y: geometry.height - 8,
        "text-anchor": atEnd ? "end" : "middle" }, label);
      tip(text, label);
      group.append(text);
    });
    return group;
  }

  lines(placed, geometry, field, className) {
    const group = svg("g", { class: className });
    for (const agentId of this.agentIds()) {
      const coords = placed.filter(({ score }) => score.message.agent_id === agentId && score[field] != null)
        .map(({ score, x }) => `${x.toFixed(1)},${geometry.y(score[field]).toFixed(1)}`);
      if (coords.length < 2) continue;
      group.append(svg("polyline", { points: coords.join(" "), stroke: color(agentId, this.context.agents) }));
    }
    return group;
  }

  point({ score, x }, geometry) {
    const { message } = score;
    const jump = this.jumps.find((entry) => entry.score === score);
    const node = svg("g", { class: "echo-point" + (jump ? " is-jump" : ""), transform: `translate(${x.toFixed(1)} ${geometry.y(score.echo).toFixed(1)})` });
    if (jump) node.append(svg("circle", { class: "echo-jump-ring", r: 7 }));
    node.append(svg("circle", { class: "echo-dot", r: 3.5, fill: color(message.agent_id, this.context.agents) }));
    tip(node, this.pointTitle(score));
    node.addEventListener("click", () => this.actions.select(message));
    node.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this.actions.contextMenu(message, message.position, { x: event.clientX, y: event.clientY });
    });
    return { score, node };
  }

  pointTitle(score) {
    const stage = stageOf(score.message) ?? `#${formatNumber(score.message.position)}`;
    const copies = score.top?.share ? ` · copies ${this.name(score.top.source.agent_id)}` : "";
    return `${this.name(score.message.agent_id)} · ${stage} · echo ${percent(score.echo)} · self ${percent(score.selfEcho)}${copies}`;
  }

  paintPoints() {
    const { cursor, selectedId } = this.context;
    for (const { score, node } of this.points ?? []) {
      node.classList.toggle("is-future", score.message.position > cursor);
      node.classList.toggle("is-selected", score.message.id === selectedId);
    }
  }

  renderJumps() {
    const title = el("h3", "section-title", "Biggest jumps");
    if (!this.jumps.length) {
      this.jumpList.replaceChildren(title, el("p", "muted", "No rise of 30 points or more."));
      return;
    }
    const list = el("ol", "echo-jump-list");
    for (const jump of this.jumps) {
      const { message, top } = jump.score;
      const stage = stageOf(message) ?? `#${formatNumber(message.position)}`;
      const copies = top?.share ? ` · copies ${this.name(top.source.agent_id)}` : "";
      const row = button("", () => this.actions.select(message), "ghost echo-jump");
      row.classList.toggle("is-future", message.position > this.context.cursor);
      row.setAttribute("aria-current", String(message.id === this.context.selectedId));
      row.style.setProperty("--agent", color(message.agent_id, this.context.agents));
      row.append(el("span", "echo-swatch"), el("span", "echo-jump-text", `${this.name(message.agent_id)} · ${stage} · ${percent(jump.from)} → ${percent(jump.score.echo)}${copies}`));
      const item = el("li");
      item.append(row);
      list.append(item);
    }
    this.jumpList.replaceChildren(title, list);
  }

  renderDetail() {
    const score = this.scoreById.get(this.context.selectedId);
    this.detail.hidden = !score;
    if (!score) {
      this.detail.replaceChildren();
      return;
    }
    const { message } = score;
    const heading = el("div", "echo-detail-head");
    heading.append(avatar(this.context.agents?.[message.agent_id] ?? { name: this.name(message.agent_id) }),
      el("span", "", `${this.name(message.agent_id)} · ${stageOf(message) ?? `#${formatNumber(message.position)}`}`));
    if (score.pending) {
      this.detail.replaceChildren(heading, el("p", "muted", "Reading…"));
      return;
    }
    const rows = el("dl", "echo-stats");
    rows.append(el("dt", "", "Echo"), el("dd", "", score.echo == null ? "No sources" : percent(score.echo)));
    rows.append(el("dt", "", "Top source"), el("dd", "", ""));
    if (score.top?.share) {
      const source = button("", () => this.actions.select(score.top.source), "ghost echo-source");
      source.append(avatar(this.context.agents?.[score.top.source.agent_id] ?? { name: this.name(score.top.source.agent_id) }),
        el("span", "", `${this.name(score.top.source.agent_id)} · ${percent(score.top.share)}`));
      rows.lastChild.append(source);
    } else rows.lastChild.textContent = "—";
    rows.append(el("dt", "", "Self-echo"), el("dd", "", percent(score.selfEcho)));
    this.detail.replaceChildren(heading, rows);
  }

  destroy() {
    this.loader.stop();
    clearTimeout(this.refreshTimer);
    this.resizeObserver.disconnect();
    this.selfToggle.remove();
    this.root.classList.remove("echo");
    this.root.replaceChildren();
  }
}

export const echoScore = {
  id: "echo-score",
  title: "Echo",
  about: {
    question: "Are agents reasoning, or copying what they read?",
    read: "One line per agent: how much of each answer is copied from the messages it read, by stage. A sharp rise means an agent started copying. Self-echo (toggle) is how much an agent repeats its own previous answer.",
    method: "Containment: the share of the answer's 5-word phrases (shingles) that also appear in the messages it read.",
    source: { label: "Broder (1997), On the resemblance and containment of documents", url: "https://doi.org/10.1109/SEQUEN.1997.666900" },
  },
  mount(root, actions, toolbar) {
    const view = new EchoView(root, actions, toolbar);
    return { update: (context) => view.update(context), destroy: () => view.destroy() };
  },
};
