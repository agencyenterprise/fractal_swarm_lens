// Activity: per-agent heatmap over stages (or equal time buckets), shaded against each agent's own median.
import { el, button, avatar, color, formatNumber, tip, recordedUsage } from "../ui.js";
import { columnScheme, columnTitle } from "./columns.js";

const SPIKE_RATIO = 2;
// A median needs a few cells before a 2x jump means anything.
const MIN_CELLS_FOR_SPIKE = 3;
const MIN_ACTIVE_BEFORE_SILENCE = 2;
const USUAL_SHARE = 0.5;
const FLAG_LIMIT = 5;
const PROBE_SIZE = 3;
const DETAIL_BUDGET = 400;
const MAX_IN_FLIGHT = 6;
const REFRESH_MS = 150;

// Per-event numbers from full details survive view switches, so returning does not re-read the run.
const detailCache = new Map();
const failedDetails = new Set();
let chosenMetric = "messages";

const isMessage = (event) => event.kind === "message.created" && Boolean(event.agent_id);
const isTool = (event) => (event.kind === "tool.started" || event.kind === "tool.completed") && Boolean(event.agent_id);
const isActivity = (event) => isMessage(event) || isTool(event);

const average = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;
const formatDecimal = (value) => formatNumber(Math.round(value * 10) / 10);

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function detailNumbers(detail) {
  const content = detail?.data?.content;
  const metadata = detail?.data?.metadata ?? {};
  return {
    length: typeof content === "string" ? content.length : String(content ?? "").length,
    outputTokens: recordedOutputTokens(metadata.usage),
    latency: Number.isFinite(metadata.latency_seconds) ? metadata.latency_seconds : undefined,
  };
}

function recordedOutputTokens(usage) {
  const tokens = recordedUsage(usage)?.output_tokens;
  return Number.isFinite(tokens) ? tokens : undefined;
}

// Averages only the events whose details carry the field. A cell whose details are all loaded but
// none carries it has no data, which is not the same as a value of 0.
function averageOfDetails(field) {
  return (events, details) => {
    const loaded = events.filter((event) => details.has(event.id));
    const values = loaded.map((event) => details.get(event.id)[field]).filter((value) => value !== undefined);
    const exact = loaded.length === events.length;
    return { value: values.length ? average(values) : null, exact, noData: exact && !values.length };
  };
}

// Each metric reads one family of events; `cell` turns a cell's events into a value.
export const METRICS = {
  messages: {
    title: "Messages", noun: "messages", reads: isMessage, details: false, tip: "Messages per agent per stage",
    cell: (events) => ({ value: events.length, exact: true }),
    describe: (value) => `${formatNumber(value)} ${value === 1 ? "message" : "messages"}`,
  },
  length: {
    title: "Length", noun: "length", reads: isMessage, details: true, tip: "Average message length in characters",
    cell(events, details) {
      const lengths = events.map((event) => details.get(event.id)?.length ?? event.preview.length);
      return { value: average(lengths), exact: events.every((event) => details.has(event.id)) };
    },
    describe: (value) => `${formatNumber(Math.round(value))} chars avg`,
  },
  tokens: {
    title: "Tokens", noun: "output tokens", reads: isMessage, details: true, field: "outputTokens",
    tip: "Average output tokens per message, where recorded",
    cell: averageOfDetails("outputTokens"),
    describe: (value) => `${formatNumber(Math.round(value))} output tokens avg`,
  },
  latency: {
    title: "Latency", noun: "latency", reads: isMessage, details: true, field: "latency",
    tip: "Average model response time per message, where recorded",
    cell: averageOfDetails("latency"),
    describe: (value) => `${formatDecimal(value)} s avg`,
  },
  tools: {
    title: "Tools", noun: "tool use", reads: isTool, details: false, tip: "Tool calls per agent per stage",
    // A call may be recorded as start + completion or as only one of them.
    cell(events) {
      const started = events.filter((event) => event.kind === "tool.started").length;
      return { value: Math.max(started, events.length - started), exact: true };
    },
    describe: (value) => `${formatNumber(value)} tool ${value === 1 ? "call" : "calls"}`,
  },
};

// The column scheme depends only on the events, so it is built once per events array.
const schemes = new WeakMap();
function schemeFor(events) {
  if (!schemes.has(events)) schemes.set(events, columnScheme(events, isActivity));
  return schemes.get(events);
}

export function agentRows(events) {
  const rows = new Map();
  for (const event of events) {
    if (event.kind === "agent.added" && event.agent_id)
      rows.set(event.agent_id, { id: event.agent_id, name: event.agent_name || event.agent_id, model: event.model, active: false });
  }
  for (const event of events.filter(isActivity)) {
    if (!rows.has(event.agent_id)) rows.set(event.agent_id, { id: event.agent_id, name: event.agent_id, active: false });
    rows.get(event.agent_id).active = true;
  }
  return [...rows.values()].filter((row) => row.active);
}

// Cells for one metric, each scaled against its agent's own median.
export function activityModel(events, metricId, details) {
  const metric = METRICS[metricId];
  const { columns, columnOf, unit } = schemeFor(events);
  const rows = agentRows(events).map((agent) => ({ agent, cells: columns.map(() => []) }));
  const rowOf = new Map(rows.map((row) => [row.agent.id, row]));
  for (const event of events) if (metric.reads(event)) rowOf.get(event.agent_id).cells[columnOf.get(event.id)].push(event);
  const cellOf = new Map();
  rows.forEach((row, rowIndex) => row.cells.forEach((cellEvents, column) =>
    cellEvents.forEach((event) => cellOf.set(event.id, { row: rowIndex, column }))));
  for (const row of rows) {
    row.cells = row.cells.map((cellEvents) => cellEvents.length
      ? { events: cellEvents, ...metric.cell(cellEvents, details) }
      : { events: cellEvents, value: null, exact: true, noData: false });
    row.median = rowMedian(row.cells);
    for (const cell of row.cells) cell.ratio = cell.value !== null && row.median ? cell.value / row.median : null;
  }
  return { metricId, columns, unit, rows, cellOf, flags: findFlags(rows, columns, metric, unit) };
}

// Exact values set the baseline once any exist, so preview-based estimates do not drag it down.
function rowMedian(cells) {
  const measured = cells.filter((cell) => cell.value !== null);
  const exact = measured.filter((cell) => cell.exact);
  const basis = exact.length ? exact : measured;
  return basis.length ? median(basis.map((cell) => cell.value)) : null;
}

export function findFlags(rows, columns, metric, unit) {
  const flags = rows.flatMap((row) => [...spikeFlags(row, columns, metric), ...silenceFlags(row, rows, columns, metric, unit)]);
  return flags.sort((a, b) => b.score - a.score || a.column - b.column).slice(0, FLAG_LIMIT);
}

function spikeFlags(row, columns, metric) {
  const measured = row.cells.filter((cell) => cell.value !== null);
  if (measured.length < MIN_CELLS_FOR_SPIKE) return [];
  return row.cells.flatMap((cell, column) => cell.exact && cell.ratio > SPIKE_RATIO
    ? [{ kind: "spike", agentId: row.agent.id, column, score: cell.ratio, event: cell.events[0],
      text: `${row.agent.name} ${formatDecimal(cell.ratio)}× usual ${metric.noun} in ${columns[column].label}` }]
    : []);
}

// Silence is judged against the agents that spoke alongside this one in its last active column:
// a stage where none of them speak either (a different role's turn, a pause) is not silence.
// A gap counts when the agent had been active in at least half of the columns its peers were.
function silenceFlags(row, rows, columns, metric, unit) {
  const activeIn = (candidate, column) => candidate.cells[column].events.length > 0;
  const flags = [];
  let peers = [], active = 0, seen = 0, gap = null;
  row.cells.forEach((cell, column) => {
    if (activeIn(row, column)) {
      if (gap) flags.push(gap);
      gap = null;
      peers = rows.filter((other) => other !== row && activeIn(other, column));
      active++;
      seen++;
      return;
    }
    if (!peers.some((peer) => activeIn(peer, column))) return;
    if (gap) gap.length++;
    else if (active >= MIN_ACTIVE_BEFORE_SILENCE && active / seen >= USUAL_SHARE)
      gap = { column, length: 1, share: active / seen };
    seen++;
  });
  if (gap) flags.push(gap);
  const quiet = metric.reads === isTool ? "no tool use" : "silent";
  return flags.map(({ column, length, share }) => ({
    kind: "silence", agentId: row.agent.id, column, length,
    // An always-active agent missing one column ranks with a 2x spike; longer gaps rank higher.
    score: 2 * share * (1 + Math.log2(length)),
    text: length === 1
      ? `${row.agent.name} ${quiet} in ${columns[column].label}`
      : `${row.agent.name} ${quiet} for ${length} ${unit} from ${columns[column].label}`,
  }));
}

export function availableMetrics(events, details) {
  const messages = events.filter(isMessage);
  const loaded = messages.map((event) => details.get(event.id)).filter(Boolean);
  return Object.keys(METRICS).filter((id) => {
    const metric = METRICS[id];
    if (metric.field) return loaded.some((numbers) => numbers[metric.field] !== undefined);
    return id === "messages" || events.some(metric.reads);
  });
}

// The first messages of distinct agents tell whether this run records tokens and latency at all.
export function probeEvents(events) {
  const seen = new Set();
  return events.filter((event) => isMessage(event) && !seen.has(event.agent_id) && seen.add(event.agent_id)).slice(0, PROBE_SIZE);
}

// Details nearest the cursor load first; huge runs only load a window around it.
export function detailWindow(events, cursor) {
  const messages = events.filter(isMessage);
  const ordered = messages.sort((a, b) => Math.abs(a.position - cursor) - Math.abs(b.position - cursor));
  return { wanted: ordered.slice(0, DETAIL_BUDGET), total: ordered.length };
}

class DetailLoader {
  constructor(load, changed) {
    this.load = load;
    this.changed = changed;
    this.inFlight = new Set();
    this.queue = [];
    this.wanted = [];
    this.stopped = false;
  }

  want(events) {
    this.wanted = events;
    this.queue = events.filter((event) => !detailCache.has(event.id) && !failedDetails.has(event.id) && !this.inFlight.has(event.id));
    this.pump();
  }

  pump() {
    while (!this.stopped && this.inFlight.size < MAX_IN_FLIGHT && this.queue.length) this.fetch(this.queue.shift());
  }

  fetch(event) {
    this.inFlight.add(event.id);
    this.load(event)
      .then((detail) => detailCache.set(event.id, detailNumbers(detail)))
      .catch((error) => {
        failedDetails.add(event.id);
        console.error(`Activity could not load event ${event.id}`, error);
      })
      .finally(() => {
        this.inFlight.delete(event.id);
        if (this.stopped) return;
        this.changed();
        this.pump();
      });
  }

  progress() {
    const loaded = this.wanted.filter((event) => detailCache.has(event.id)).length;
    const failed = this.wanted.filter((event) => failedDetails.has(event.id)).length;
    return { loaded, failed, total: this.wanted.length, settled: loaded + failed === this.wanted.length };
  }

  stop() {
    this.stopped = true;
    this.queue = [];
  }
}

function fill(hex, ratio) {
  // log2 scale centred on the agent's median: 0.5x is faint, 1x is mid, a 2x spike is full.
  const strength = Math.min(1, Math.max(0, (Math.log2(ratio) + 1) / 2));
  return `color-mix(in srgb, ${hex} ${Math.round(12 + strength * 88)}%, transparent)`;
}

class ActivityView {
  constructor(root, actions, toolbar) {
    this.root = root;
    this.actions = actions;
    this.context = null;
    this.version = 0;
    this.modelKey = null;
    this.loader = new DetailLoader((event) => actions.loadDetail(event), () => this.scheduleRefresh());
    this.metrics = el("div", "segmented act-metrics");
    this.metrics.setAttribute("role", "group");
    this.metrics.setAttribute("aria-label", "Activity metric");
    this.status = el("span", "act-status");
    this.status.setAttribute("aria-live", "polite");
    toolbar.append(this.status, this.metrics);
    this.scroll = el("div", "act-scroll");
    this.flags = el("ol", "act-flags");
    this.tip = el("div", "act-tip");
    this.tip.hidden = true;
    root.classList.add("act");
    root.replaceChildren(this.scroll, this.flags, this.tip);
    this.scroll.addEventListener("pointerover", (event) => this.showTip(event.target.closest(".act-cell")));
    this.scroll.addEventListener("focusin", (event) => this.showTip(event.target.closest(".act-cell")));
    this.scroll.addEventListener("pointerleave", () => (this.tip.hidden = true));
    this.scroll.addEventListener("focusout", () => (this.tip.hidden = true));
  }

  update(context) {
    this.context = context;
    this.requestDetails();
    this.render();
  }

  scheduleRefresh() {
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.version++;
      if (this.context) this.render();
    }, REFRESH_MS);
  }

  requestDetails() {
    const { events, cursor } = this.context;
    const probe = probeEvents(events);
    if (!METRICS[chosenMetric].details) return this.loader.want(probe);
    const { wanted, total } = detailWindow(events, cursor);
    this.windowTotal = total;
    this.loader.want([...probe, ...wanted.filter((event) => !probe.includes(event))]);
  }

  render() {
    const { events, agents } = this.context;
    const available = availableMetrics(events, detailCache);
    if (!available.includes(chosenMetric) && this.loader.progress().settled) chosenMetric = "messages";
    const colorKey = Object.keys(agents || {}).join("\n");
    const key = [events, chosenMetric, this.version, colorKey, available.join()];
    if (!this.modelKey || key.some((part, index) => part !== this.modelKey[index])) {
      this.modelKey = key;
      this.model = activityModel(events, chosenMetric, detailCache);
      this.renderMetrics(available);
      this.renderGrid();
      this.renderFlags();
    }
    this.renderStatus();
    this.renderCursor();
  }

  renderMetrics(available) {
    const ids = available.includes(chosenMetric) ? available : [...available, chosenMetric];
    this.metrics.replaceChildren(...ids.map((id) => {
      const tab = tip(button(METRICS[id].title, () => this.choose(id)), METRICS[id].tip);
      tab.setAttribute("aria-pressed", String(id === chosenMetric));
      return tab;
    }));
  }

  choose(id) {
    chosenMetric = id;
    this.requestDetails();
    this.render();
  }

  renderStatus() {
    if (!METRICS[chosenMetric].details) return (this.status.textContent = "");
    const { loaded, failed, total, settled } = this.loader.progress();
    const parts = [];
    if (!settled) parts.push(`Loading details ${formatNumber(loaded)}/${formatNumber(total)}`);
    if (this.windowTotal > DETAIL_BUDGET)
      parts.push(`${formatNumber(DETAIL_BUDGET)} of ${formatNumber(this.windowTotal)} messages near the cursor`);
    if (failed) parts.push(`${formatNumber(failed)} failed`);
    this.status.textContent = parts.join(" · ");
  }

  renderGrid() {
    const { columns, rows } = this.model;
    if (!rows.length) {
      this.grid = null;
      this.scroll.replaceChildren(el("p", "empty", "No agent messages or tool calls"));
      return;
    }
    const grid = el("div", "act-grid");
    grid.style.gridTemplateColumns = `minmax(96px, 160px) repeat(${columns.length}, minmax(22px, 1fr))`;
    grid.style.gridTemplateRows = `auto repeat(${rows.length}, 24px)`;
    grid.append(place(el("div", "act-corner"), 1, 1));
    columns.forEach((column, index) => {
      const head = place(el("div", "act-col", column.short), 1, index + 2);
      head.classList.toggle("has-marker", column.markers.length > 0);
      tip(head, columnTitle(column));
      grid.append(head);
    });
    const flagged = new Map(this.model.flags.map((flag) => [`${flag.agentId}:${flag.column}`, flag.kind]));
    rows.forEach((row, rowIndex) => {
      grid.append(place(this.agentHead(row.agent), rowIndex + 2, 1));
      row.cells.forEach((cell, column) =>
        grid.append(place(this.cellNode(row, cell, rowIndex, column, flagged.get(`${row.agent.id}:${column}`)), rowIndex + 2, column + 2)));
    });
    this.future = el("div", "act-future");
    this.cursorMark = el("div", "act-cursor");
    this.selectedMark = el("div", "act-selected");
    grid.append(this.future, this.cursorMark, this.selectedMark);
    this.grid = grid;
    this.scroll.replaceChildren(grid);
  }

  agentHead(agent) {
    const head = button("", () => this.actions.selectAgent(agent.id), "act-agent");
    head.append(avatar(agent), el("span", "act-name", agent.name));
    tip(head, `${agent.name} · select agent`);
    return head;
  }

  cellNode(row, cell, rowIndex, column, flag) {
    const node = button("", () => cell.events.length && this.actions.select(cell.events[0]), "act-cell");
    node.dataset.row = rowIndex;
    node.dataset.column = column;
    if (flag) node.classList.add(`is-${flag}`);
    if (!cell.events.length) node.classList.add("is-empty");
    else if (cell.noData) node.classList.add("is-nodata");
    else if (cell.value === null) node.classList.add("is-pending");
    else node.style.background = fill(color(row.agent.id, this.context.agents), cell.ratio ?? 1);
    if (!cell.exact) node.classList.add("is-approx");
    if (!cell.events.length) node.tabIndex = -1;
    node.setAttribute("aria-label", `${row.agent.name}, ${this.model.columns[column].label}: ${this.cellText(cell)}`);
    return node;
  }

  cellText(cell) {
    const metric = METRICS[this.model.metricId];
    if (!cell.events.length) return "none";
    if (cell.noData) return `no data · ${METRICS.messages.describe(cell.events.length)}`;
    if (cell.value === null) return "details not loaded";
    const parts = [`${cell.exact ? "" : "≈"}${metric.describe(cell.value)}`];
    if (metric.reads === isMessage && this.model.metricId !== "messages") parts.push(METRICS.messages.describe(cell.events.length));
    if (cell.ratio !== null) parts.push(`${formatDecimal(cell.ratio)}× usual`);
    return parts.join(" · ");
  }

  showTip(node) {
    if (!node) return (this.tip.hidden = true);
    const row = this.model.rows[node.dataset.row];
    const column = this.model.columns[node.dataset.column];
    this.tip.replaceChildren(el("strong", "", `${row.agent.name} · ${column.label}`),
      el("span", "", this.cellText(row.cells[node.dataset.column])));
    this.tip.hidden = false;
    const bounds = this.root.getBoundingClientRect();
    const cell = node.getBoundingClientRect();
    const left = Math.min(cell.left - bounds.left, bounds.width - this.tip.offsetWidth - 8);
    this.tip.style.left = `${Math.max(8, left)}px`;
    this.tip.style.top = `${cell.bottom - bounds.top + 4}px`;
  }

  renderFlags() {
    const { flags, rows } = this.model;
    if (!rows.length) return this.flags.replaceChildren();
    if (!flags.length) return this.flags.replaceChildren(el("li", "act-calm muted", "No outliers"));
    this.flags.replaceChildren(...flags.map((flag) => {
      const item = el("li");
      const row = rows.find((candidate) => candidate.agent.id === flag.agentId);
      const target = button("", () => this.openFlag(flag), `act-flag is-${flag.kind}`);
      target.dataset.column = flag.column;
      target.append(avatar(row.agent), el("span", "", flag.text));
      item.append(target);
      return item;
    }));
  }

  openFlag(flag) {
    if (flag.event) this.actions.select(flag.event);
    else this.actions.seek(this.model.columns[flag.column].start);
  }

  renderCursor() {
    if (!this.grid) return;
    const { columns, rows } = this.model;
    const { cursor, selectedId } = this.context;
    const current = columns.findLastIndex((column) => column.start <= cursor);
    const firstFuture = current + 1;
    this.future.hidden = firstFuture >= columns.length;
    this.future.style.gridArea = `1 / ${firstFuture + 2} / ${rows.length + 2} / ${columns.length + 2}`;
    this.cursorMark.hidden = current < 0;
    this.cursorMark.style.gridArea = `1 / ${current + 2} / ${rows.length + 2} / ${current + 3}`;
    for (const flag of this.flags.querySelectorAll(".act-flag"))
      flag.classList.toggle("is-future", Number(flag.dataset.column) > current);
    const selected = this.model.cellOf.get(selectedId);
    this.selectedMark.hidden = !selected;
    if (selected) this.selectedMark.style.gridArea = `${selected.row + 2} / ${selected.column + 2}`;
  }

  destroy() {
    this.loader.stop();
    clearTimeout(this.refreshTimer);
    this.metrics.remove();
    this.status.remove();
    this.root.classList.remove("act");
    this.root.replaceChildren();
  }
}

function place(node, row, column) {
  node.style.gridArea = `${row} / ${column}`;
  return node;
}

export const activityStrip = {
  id: "activity-strip",
  title: "Activity",
  about: {
    question: "Who is unusually loud, quiet, slow or verbose?",
    read: "Rows are agents, columns are stages (or equal time spans when stage labels do not describe phases). Shading compares each cell with that agent's own usual value, so a spike stands out per agent. Flags list cells over 2× usual and silences.",
    method: "Counts and averages per stage; tokens and latency appear only when the run recorded them.",
  },
  mount(root, actions, toolbar) {
    const view = new ActivityView(root, actions, toolbar);
    return { update: (context) => view.update(context), destroy: () => view.destroy() };
  },
};
