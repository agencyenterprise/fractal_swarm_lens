// Phrase spread: where a phrase shows up per speaker and stage, and in which order it reached each agent.
import { el, button, svg, color, avatar, stageOf, speakerName, formatNumber, tip } from "../ui.js";

const MESSAGE = "message.created";
const SCAN_LIMIT = 2000;
const CONCURRENCY = 6;
const MAX_STAGE_COLUMNS = 60;
const BUCKET_COLUMNS = 40;
// event_summary truncates previews at 260 characters, so a shorter preview is already the full text.
const PREVIEW_LIMIT = 260;
const DEBOUNCE_MS = 250;
const RENDER_MS = 200;
const ROW_HEIGHT = 28;
const SHADE_LEVELS = 4;
const MIN_TOKEN_LENGTH = 4;
const SUGGESTION_COUNT = 5;
const MIN_AGENTS_REACHED = 2;
const STOPWORDS = new Set(("about above after again against along also although among another because been before being below between both " +
  "cannot could does doing down during each either even every from further have having here hers herself himself however " +
  "into itself just more most much must myself neither only other ought ours ourselves over regarding same should since some such " +
  "than that their theirs them themselves then there therefore these they this those through thus under until upon very " +
  "were what when where which while whom whose will with within without would your yours yourself yourselves").split(" "));

// Lowercased full message text by event id, kept across mounts so switching views does not rescan.
const texts = new Map();
let trackedQuery = "";

export function textOf(event) {
  return texts.get(event.id) ?? (event.preview || "").toLowerCase();
}

function contentOf(detail) {
  if (!detail?.data) throw new Error(`Event ${detail?.id ?? "?"} has no data`);
  const { content } = detail.data;
  if (content == null) return "";
  return (typeof content === "string" ? content : JSON.stringify(content)).toLowerCase();
}

// Loads full text for `events` with at most CONCURRENCY requests in flight; stops at the first failure.
export function scanTexts(events, loadDetail, onProgress) {
  const queue = [];
  for (const event of events) {
    if (texts.has(event.id)) continue;
    if ((event.preview || "").length < PREVIEW_LIMIT) texts.set(event.id, (event.preview || "").toLowerCase());
    else queue.push(event);
  }
  let stopped = false;
  const worker = async () => {
    while (!stopped && queue.length) {
      const event = queue.shift();
      try {
        const detail = await loadDetail(event);
        if (stopped) return;
        texts.set(event.id, contentOf(detail));
      } catch (error) {
        stopped = true;
        throw error;
      }
      onProgress();
    }
  };
  const done = Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
  return { done, stop: () => { stopped = true; } };
}

function speakerRows(events, messages) {
  const names = new Map();
  for (const event of events)
    if (event.kind === "agent.added" || event.kind === "agent.updated")
      names.set(event.agent_id, event.agent_name || names.get(event.agent_id) || event.agent_id);
  for (const event of messages)
    if (event.agent_id && !names.has(event.agent_id)) names.set(event.agent_id, event.agent_id);
  const rows = [...names].map(([id, name]) => ({ id, name, isAgent: true }));
  const anonymous = messages.find((event) => !event.agent_id);
  if (anonymous) rows.unshift({ id: null, name: speakerName(anonymous, {}), isAgent: false });
  return rows;
}

// Numbered stages ("Debate round 2" after "Debate round 1") keep only their number in the header.
function shortLabels(labels) {
  return labels.map((label, index) => {
    const [, prefix, number] = label.match(/^(.*\D)(\d+)$/) || [];
    const previous = labels[index - 1]?.match(/^(.*\D)(\d+)$/);
    return prefix && previous?.[1] === prefix ? number : label;
  });
}

function stageColumns(messages) {
  if (!messages.some(stageOf)) return null;
  const labels = [];
  let current = "Start";
  const columnOf = messages.map((event) => {
    current = stageOf(event) ?? current;
    if (!labels.includes(current)) labels.push(current);
    return labels.indexOf(current);
  });
  return labels.length > MAX_STAGE_COLUMNS ? null : { labels, columnOf };
}

function bucketColumns(messages) {
  const size = Math.max(1, Math.ceil(messages.length / BUCKET_COLUMNS));
  const labels = [];
  for (let start = 0; start < messages.length; start += size)
    labels.push(`Messages ${start + 1}–${Math.min(start + size, messages.length)}`);
  return { labels, columnOf: messages.map((_, index) => Math.floor(index / size)) };
}

// Rows are speakers, columns are stages (or buckets of consecutive messages), cells hold messages.
export function spreadGrid(events) {
  const messages = events.filter((event) => event.kind === MESSAGE);
  const rows = speakerRows(events, messages);
  const rowIndex = new Map(rows.map((row, index) => [row.id, index]));
  const { labels, columnOf } = stageColumns(messages) ?? bucketColumns(messages);
  const short = shortLabels(labels);
  const columns = labels.map((label, index) => ({ label, short: short[index] }));
  const cells = new Map();
  const cellOf = new Map();
  const placed = messages.map((event, index) => {
    const row = rowIndex.get(event.agent_id || null);
    const key = `${row}:${columnOf[index]}`;
    if (!cells.has(key)) cells.set(key, { row, column: columnOf[index], events: [] });
    cells.get(key).events.push(event);
    cellOf.set(event.id, key);
    return { event, row, column: columnOf[index] };
  });
  return { rows, columns, messages: placed, cells, cellOf };
}

// Matching messages per cell and the first match of each speaker, in the order the phrase reached them.
export function findSpread(grid, query) {
  const needle = query.trim().toLowerCase();
  const counts = new Map();
  const firstByCell = new Map();
  const firstByRow = new Map();
  let hits = 0;
  for (const { event, row, column } of grid.messages) {
    if (!textOf(event).includes(needle)) continue;
    hits += 1;
    const key = `${row}:${column}`;
    counts.set(key, (counts.get(key) || 0) + 1);
    if (!firstByCell.has(key)) firstByCell.set(key, event);
    if (!firstByRow.has(row)) firstByRow.set(row, { row, column, event });
  }
  return { hits, counts, firstByCell, jumps: [...firstByRow.values()], maxCount: Math.max(0, ...counts.values()) };
}

function tokensOf(text) {
  return new Set((text.match(/[\p{L}\p{N}]+/gu) || [])
    .filter((token) => token.length >= MIN_TOKEN_LENGTH && !STOPWORDS.has(token)));
}

function firstUses(grid) {
  const uses = new Map();
  for (const { event, row, column } of grid.messages)
    for (const token of tokensOf(textOf(event))) {
      if (!uses.has(token)) uses.set(token, new Map());
      const byRow = uses.get(token);
      if (!byRow.has(row)) byRow.set(row, { column, position: event.position });
    }
  return uses;
}

// Words an agent used first that other agents only picked up in a later column. Agents already using the
// word in the origin's own column count as independent use, which ranks a word lower.
export function contagiousTokens(grid, limit = SUGGESTION_COUNT) {
  const ranked = [];
  for (const [token, byRow] of firstUses(grid)) {
    const [originRow, origin] = byRow.entries().next().value;
    if (!grid.rows[originRow].isAgent) continue;
    const others = [...byRow].filter(([row]) => row !== originRow && grid.rows[row].isAgent);
    const reached = others.filter(([, use]) => use.column > origin.column).length;
    if (reached < MIN_AGENTS_REACHED) continue;
    ranked.push({ token, row: originRow, reached, independent: others.length - reached, position: origin.position });
  }
  return ranked
    .sort((a, b) => b.reached - a.reached || a.independent - b.independent || a.position - b.position
      || a.token.localeCompare(b.token))
    .slice(0, limit);
}

class PhraseSpreadView {
  constructor(root, actions, toolbar) {
    this.root = root;
    this.actions = actions;
    this.toolbar = toolbar;
    this.context = null;
    this.events = null;
    this.scanAll = false;
    this.scan = null;
    this.scanError = null;
    this.suggestions = null;
    this.cellNodes = [];
    this.markNodes = [];
    this.build();
  }

  build() {
    this.input = el("input", "ps-search");
    this.input.type = "search";
    this.input.placeholder = "Track a phrase…";
    this.input.setAttribute("aria-label", "Track a phrase");
    this.input.value = trackedQuery;
    this.input.addEventListener("input", () => this.debounceQuery());
    this.input.addEventListener("keydown", (event) => this.onSearchKey(event));
    this.toolbar.append(this.input);
    this.status = el("div", "ps-status");
    this.status.setAttribute("aria-live", "polite");
    this.plot = el("div", "ps-plot");
    this.root.classList.add("ps");
    this.root.replaceChildren(this.status, this.plot);
  }

  onSearchKey(event) {
    if (event.key === "Enter") this.setQuery(this.input.value);
    else if (event.key === "Escape") this.setQuery("");
  }

  debounceQuery() {
    clearTimeout(this.queryTimer);
    this.queryTimer = setTimeout(() => this.setQuery(this.input.value), DEBOUNCE_MS);
  }

  setQuery(value) {
    clearTimeout(this.queryTimer);
    this.input.value = value;
    if (value.trim() === trackedQuery.trim()) return;
    trackedQuery = value;
    this.render();
  }

  update(context) {
    this.context = context;
    if (context.events !== this.events) {
      this.events = context.events;
      this.grid = spreadGrid(context.events);
      this.startScan();
      this.render();
    } else this.paint();
  }

  scanWindow() {
    const messages = this.grid.messages.map(({ event }) => event);
    return this.scanAll ? messages : messages.slice(0, SCAN_LIMIT);
  }

  startScan() {
    this.scan?.stop();
    this.scanError = null;
    this.suggestions = null;
    const scan = scanTexts(this.scanWindow(), this.actions.loadDetail, () => this.scheduleRender());
    this.scan = scan;
    scan.done.then(() => this.scan === scan && this.scheduleRender(), (error) => {
      if (this.scan !== scan) return;
      console.error(error);
      this.scanError = error;
      this.scheduleRender();
    });
  }

  scheduleRender() {
    if (this.renderTimer) return;
    this.renderTimer = setTimeout(() => {
      this.renderTimer = null;
      this.render();
    }, RENDER_MS);
  }

  render() {
    if (!this.grid) return;
    const query = trackedQuery.trim();
    const spread = query ? findSpread(this.grid, query) : null;
    this.renderStatus(spread);
    this.renderGrid(spread);
    this.paint();
  }

  scanProgress() {
    const window = this.scanWindow();
    return { scanned: window.filter((event) => texts.has(event.id)).length, size: window.length,
      total: this.grid.messages.length };
  }

  renderStatus(spread) {
    const progress = this.scanProgress();
    const parts = [];
    if (spread) parts.push(el("span", "ps-summary", this.summary(spread)));
    if (this.scanError) {
      parts.push(el("span", "ps-error", `Scan stopped: ${this.scanError.message}`),
        button("Retry", () => this.startScan(), "ghost ps-action"));
    } else if (progress.scanned < progress.size) {
      parts.push(el("span", "muted", `Scanning ${formatNumber(progress.scanned)} / ${formatNumber(progress.size)} messages`));
    } else if (progress.size < progress.total) {
      parts.push(el("span", "muted", `Scanned first ${formatNumber(progress.size)} of ${formatNumber(progress.total)} messages`),
        button("Scan all", () => this.expandScan(), "ghost ps-action"));
    }
    if (!spread && progress.scanned === progress.size && !this.scanError) parts.push(...this.suggestionNodes());
    this.status.replaceChildren(...parts);
  }

  expandScan() {
    this.scanAll = true;
    this.startScan();
    this.render();
  }

  summary(spread) {
    const total = formatNumber(this.grid.messages.length);
    if (!spread.hits) return `No match in ${total} messages`;
    const [first] = spread.jumps;
    const agents = spread.jumps.filter(({ row }) => this.grid.rows[row].isAgent).length;
    return `Appears in ${formatNumber(spread.hits)} of ${total} messages · first by ${this.grid.rows[first.row].name}` +
      ` in ${this.grid.columns[first.column].label} · reached ${agents} agent${agents === 1 ? "" : "s"}`;
  }

  suggestionNodes() {
    this.suggestions ??= contagiousTokens(this.grid);
    if (!this.suggestions.length) return [el("span", "muted", "No word spread from one agent to others")];
    return [tip(el("span", "muted", "Spreading"), "Words one agent introduced and others used later; click one to track it"), ...this.suggestions.map((item) => {
      const chip = button(item.token, () => this.setQuery(item.token), "ps-chip");
      chip.append(el("span", "ps-chip-count", String(item.reached)));
      tip(chip, `First used by ${this.grid.rows[item.row].name}, then by ${item.reached} other agent${item.reached === 1 ? "" : "s"}`);
      return chip;
    })];
  }

  renderGrid(spread) {
    const { rows, columns } = this.grid;
    if (!this.grid.messages.length) {
      this.cellNodes = [];
      this.markNodes = [];
      this.plot.replaceChildren(el("div", "empty", "No messages"));
      return;
    }
    const grid = el("div", "ps-grid");
    grid.style.setProperty("--ps-columns", columns.length);
    grid.style.setProperty("--ps-rows", rows.length);
    grid.style.setProperty("--ps-row", `${ROW_HEIGHT}px`);
    grid.append(el("span", "ps-corner"), ...columns.map(columnHeader));
    this.cellNodes = [];
    rows.forEach((row, rowIndex) => {
      grid.append(this.rowLabel(row));
      columns.forEach((_, column) => grid.append(this.cell(rowIndex, column, spread)));
    });
    grid.append(this.jumpLayer(spread));
    this.plot.replaceChildren(grid);
  }

  rowLabel(row) {
    const label = row.isAgent
      ? button("", () => this.actions.selectAgent(row.id), "ps-row-label")
      : el("span", "ps-row-label");
    const agent = this.context.agents?.[row.id] || { name: row.name };
    label.append(avatar(agent), el("span", "ps-row-name", row.name));
    label.style.setProperty("--ps-hue", this.hue(row));
    tip(label, row.name);
    return label;
  }

  hue(row) {
    return row.isAgent ? color(row.id, this.context.agents) : "var(--text-3)";
  }

  cell(row, column, spread) {
    const key = `${row}:${column}`;
    const entry = this.grid.cells.get(key);
    if (!entry) return el("span", "ps-cell is-void");
    const count = spread?.counts.get(key) || 0;
    const target = spread ? spread.firstByCell.get(key) : entry.events[0];
    const node = button("", () => target && this.actions.select(target), "ps-cell");
    node.disabled = !target;
    node.dataset.level = spread ? Math.ceil((count / (spread.maxCount || 1)) * SHADE_LEVELS) : "";
    node.style.setProperty("--ps-hue", this.hue(this.grid.rows[row]));
    const where = `${this.grid.rows[row].name} · ${this.grid.columns[column].label}`;
    const size = entry.events.length;
    const summary = spread ? `${where} · ${count} of ${size} messages` : `${where} · ${size} message${size === 1 ? "" : "s"}`;
    tip(node, summary);
    node.setAttribute("aria-label", summary);
    this.cellNodes.push({ node, key, first: entry.events[0].position });
    return node;
  }

  // Numbered markers at each speaker's first match, joined in the order the phrase reached them.
  jumpLayer(spread) {
    const layer = svg("svg", { class: "ps-jumps", "aria-hidden": "true" });
    this.markNodes = [];
    const jumps = spread?.jumps || [];
    const x = ({ column }) => `${((column + 0.5) / this.grid.columns.length) * 100}%`;
    const y = ({ row }) => (row + 0.5) * ROW_HEIGHT;
    jumps.slice(1).forEach((jump, index) => {
      const from = jumps[index];
      const line = svg("line", { class: "ps-jump", x1: x(from), y1: y(from), x2: x(jump), y2: y(jump) });
      layer.append(line);
      this.markNodes.push({ node: line, position: jump.event.position });
    });
    jumps.forEach((jump, index) => {
      const mark = svg("g", { class: index === 0 ? "ps-mark is-first" : "ps-mark" });
      mark.style.setProperty("--ps-hue", this.hue(this.grid.rows[jump.row]));
      mark.append(svg("circle", { cx: x(jump), cy: y(jump), r: 8 }),
        svg("text", { x: x(jump), y: y(jump) }, String(index + 1)));
      layer.append(mark);
      this.markNodes.push({ node: mark, position: jump.event.position });
    });
    return layer;
  }

  // Cursor and selection only touch classes, so moving the cursor never rebuilds the grid.
  paint() {
    if (!this.context) return;
    const { cursor, selectedId } = this.context;
    const selectedKey = selectedId && this.grid.cellOf.get(selectedId);
    for (const { node, key, first } of this.cellNodes) {
      node.classList.toggle("is-future", first > cursor);
      node.classList.toggle("is-selected", key === selectedKey);
    }
    for (const { node, position } of this.markNodes) node.classList.toggle("is-future", position > cursor);
  }

  destroy() {
    this.scan?.stop();
    this.scan = null;
    clearTimeout(this.queryTimer);
    clearTimeout(this.renderTimer);
    this.input.remove();
    this.root.replaceChildren();
    this.root.classList.remove("ps");
  }
}

function columnHeader(column) {
  const node = el("span", "ps-column", column.short);
  tip(node, column.label);
  return node;
}

export const phraseSpread = {
  id: "phrase-spread",
  title: "Phrase spread",
  about: {
    question: "Where does a phrase appear, and when did it jump to another agent?",
    read: "Type a phrase. Each cell is one agent in one stage; darker means more matches. Numbered markers give the order in which agents first used it. Suggested words are ones one agent introduced and others picked up later.",
    method: "Case-insensitive literal match on the full text of each message.",
  },
  mount(root, actions, toolbar) {
    const view = new PhraseSpreadView(root, actions, toolbar);
    return { update: (context) => view.update(context), destroy: () => view.destroy() };
  },
};
