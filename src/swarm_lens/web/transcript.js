import { el, button, avatar, color, eventTone, stageOf, speakerName, time, formatNumber, failure } from "./ui.js";
import { renderMarkdown } from "./markdown.js";

const PAGE = 150;
const LATER_PAGE = 50;
const MESSAGE_TONES = new Set(["message", "risk", "intervention"]);
const CALLOUT_TONES = new Set(["risk", "intervention"]);
const ALL_TONES = new Set(["message", "risk", "observation", "intervention", "tool", "memory"]);
const INTERVENTION_LABELS = {
  "agent.updated": "Prompt changed",
  "agent.removed": "Agent removed",
  "agent.added": "Agent added",
  "environment.updated": "Goal changed",
  "memory.written": "Memory edited",
};
const KIND_LABELS = { tool: "Tool", memory: "Memory", observation: "Observation" };

export function interventionLabel(event) {
  return INTERVENTION_LABELS[event.kind] || event.stage_label || "Intervention";
}

// Events the transcript shows for the given filters, oldest first.
export function visibleEvents(events, { agentId, mode, query, agents }) {
  const tones = mode === "all" ? ALL_TONES : MESSAGE_TONES;
  const needle = query.trim().toLowerCase();
  return events.filter((event) =>
    (!agentId || event.agent_id === agentId)
    && tones.has(eventTone(event))
    && (!needle || `${speakerName(event, agents)} ${event.preview || ""}`.toLowerCase().includes(needle)));
}

// Interleaves a stage header before each run of events sharing a new stage.
// Callouts carry their own label, so they never open a stage of their own.
export function groupByStage(events) {
  const rows = [];
  let current = null;
  for (const event of events) {
    const stage = CALLOUT_TONES.has(eventTone(event)) ? null : stageOf(event);
    if (stage && stage !== current) rows.push({ type: "stage", key: `stage:${event.id}`, label: stage });
    if (stage) current = stage;
    rows.push({ type: "event", key: event.id, event });
  }
  return rows;
}

export class Transcript {
  constructor(root, { onSelect, loadDetail, onClearAgent }) {
    this.root = root;
    this.handlers = { onSelect, loadDetail, onClearAgent };
    this.mode = "messages";
    this.query = "";
    this.extra = { before: 0, after: 0 };
    this.nodes = new Map();
    this.loaded = new Set();
    this.props = null;
    this.buildShell();
    // Entries before the cursor prefetch ahead of scrolling; entries after it load only once on screen.
    this.observer = new IntersectionObserver((entries) => this.onIntersect(entries), {
      root: this.list,
      rootMargin: "600px 0px",
    });
    this.lateObserver = new IntersectionObserver((entries) => this.onIntersect(entries), { root: this.list });
    document.addEventListener("keydown", (event) => this.onGlobalKey(event));
  }

  buildShell() {
    this.toolbar = el("div", "tx-toolbar");
    this.modes = el("div", "segmented");
    this.modes.setAttribute("role", "group");
    this.modes.setAttribute("aria-label", "Show");
    for (const [mode, label] of [["messages", "Messages"], ["all", "All"]]) {
      const node = button(label, () => this.setFilter({ mode }));
      node.dataset.mode = mode;
      this.modes.append(node);
    }
    this.search = el("input", "tx-search");
    this.search.type = "search";
    this.search.placeholder = "Search";
    this.search.setAttribute("aria-label", "Search transcript");
    this.search.addEventListener("input", () => this.setFilter({ query: this.search.value }));
    this.search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        this.search.value = "";
        this.setFilter({ query: "" });
        this.search.blur();
      }
    });
    this.chip = el("span", "tx-chip");
    this.count = el("span", "tx-count muted");
    this.toolbar.append(this.modes, this.search, this.chip, this.count);
    this.list = el("div", "tx-list");
    this.earlier = button("Show earlier", () => this.showMore("before", PAGE), "ghost tx-page");
    this.later = button("Show later", () => this.showMore("after", LATER_PAGE), "ghost tx-page");
    this.empty = el("p", "empty");
    this.root.replaceChildren(this.toolbar, this.list);
  }

  render(props) {
    const previous = this.props;
    if (previous && previous.events !== props.events) this.prune(props.events);
    const agentChanged = Boolean(previous) && previous.agentId !== props.agentId;
    if (agentChanged) this.resetWindow();
    this.props = props;
    this.update({ scroll: !previous || agentChanged || previous.cursor !== props.cursor || previous.events !== props.events });
  }

  setFilter(change) {
    Object.assign(this, change);
    this.resetWindow();
    if (this.props) this.update({ scroll: true });
  }

  resetWindow() {
    this.extra = { before: 0, after: 0 };
  }

  showMore(side, count) {
    this.extra[side] += count;
    this.update({ scroll: false });
  }

  update({ scroll }) {
    const { events, agents, cursor, selectedId, agentId } = this.props;
    const matching = visibleEvents(events, { agentId, mode: this.mode, query: this.query, agents });
    const split = matching.findIndex((event) => event.position > cursor);
    const boundary = split === -1 ? matching.length : split;
    const start = Math.max(0, boundary - PAGE - this.extra.before);
    const end = Math.min(matching.length, boundary + LATER_PAGE + this.extra.after);
    const shown = matching.slice(start, end);
    this.renderToolbar(matching.length, agents, agentId);
    const rows = groupByStage(shown).map((row) => this.nodeFor(row));
    if (start > 0) rows.unshift(this.earlier);
    if (end < matching.length) rows.push(this.later);
    if (!rows.length) {
      this.empty.textContent = events.length ? "No matches" : "No events yet";
      rows.push(this.empty);
    }
    this.reconcile(rows);
    const atCursor = matching[boundary - 1];
    for (const event of shown) {
      const node = this.nodes.get(event.id);
      node.classList.toggle("tx-selected", event.id === selectedId);
      node.classList.toggle("tx-at-cursor", event === atCursor);
      this.setFuture(node, event.position > cursor);
    }
    const anchor = atCursor || shown[0];
    if (scroll && anchor) this.nodes.get(anchor.id).scrollIntoView({ block: "nearest" });
  }

  setFuture(node, future) {
    node.classList.toggle("tx-future", future);
    if (!node.classList.contains("tx-message") || this.loaded.has(node.dataset.id)) return;
    const watch = future ? "late" : "early";
    if (node.dataset.watch === watch) return;
    this.observer.unobserve(node);
    this.lateObserver.unobserve(node);
    (future ? this.lateObserver : this.observer).observe(node);
    node.dataset.watch = watch;
  }

  renderToolbar(total, agents, agentId) {
    for (const node of this.modes.children) node.setAttribute("aria-pressed", String(node.dataset.mode === this.mode));
    const noun = this.mode === "messages" ? "message" : "event";
    this.count.textContent = `${formatNumber(total)} ${noun}${total === 1 ? "" : "s"}`;
    this.chip.hidden = !agentId;
    if (!agentId) return;
    const clear = button("×", () => this.handlers.onClearAgent(), "icon ghost");
    clear.setAttribute("aria-label", "Clear agent filter");
    this.chip.replaceChildren(el("span", "", agents[agentId]?.name || agentId), clear);
  }

  // Moves existing nodes into the desired order so a cursor step only touches what changed.
  reconcile(rows) {
    const wanted = new Set(rows);
    for (const child of [...this.list.children]) if (!wanted.has(child)) child.remove();
    rows.forEach((node, index) => {
      if (this.list.children[index] !== node) this.list.insertBefore(node, this.list.children[index] || null);
    });
  }

  prune(events) {
    const ids = new Set(events.map((event) => event.id));
    for (const [key, node] of this.nodes) {
      const id = key.startsWith("stage:") ? key.slice(6) : key;
      if (ids.has(id)) continue;
      this.observer.unobserve(node);
      this.lateObserver.unobserve(node);
      node.remove();
      this.nodes.delete(key);
      this.loaded.delete(id);
    }
  }

  nodeFor(row) {
    let node = this.nodes.get(row.key);
    if (!node) {
      node = row.type === "stage" ? el("div", "tx-stage", row.label) : this.entry(row.event);
      this.nodes.set(row.key, node);
    }
    return node;
  }

  entry(event) {
    const tone = eventTone(event);
    const node = tone === "message" ? this.messageEntry(event)
      : CALLOUT_TONES.has(tone) ? this.calloutEntry(event, tone)
        : this.compactEntry(event, tone);
    node.tabIndex = 0;
    node.dataset.id = event.id;
    node.addEventListener("click", () => this.handlers.onSelect(event));
    node.addEventListener("keydown", (key) => {
      if (key.target === node && (key.key === "Enter" || key.key === " ")) {
        key.preventDefault();
        this.handlers.onSelect(event);
      }
    });
    return node;
  }

  messageEntry(event) {
    const { agents } = this.props;
    const node = el("article", "tx-entry tx-message");
    const head = el("div", "tx-head");
    const agent = agents[event.agent_id];
    const name = el("span", "tx-speaker", speakerName(event, agents));
    if (agent) name.style.color = color(event.agent_id, agents);
    head.append(avatar(agent || { name: name.textContent }), name, el("time", "tx-time mono", time(event.at)));
    const body = el("div", "md tx-body");
    body.append(renderMarkdown(event.preview || ""));
    const more = button("Show more", (click) => {
      click.stopPropagation();
      const expanded = node.classList.toggle("tx-expanded");
      more.textContent = expanded ? "Show less" : "Show more";
    }, "ghost tx-more");
    more.hidden = true;
    node.append(head, body, more);
    return node;
  }

  calloutEntry(event, tone) {
    const node = el("article", `tx-entry tx-callout tx-${tone}`);
    const agent = this.props.agents[event.agent_id];
    const label = tone === "risk" ? event.stage_label || event.label : interventionLabel(event);
    const head = el("div", "tx-head");
    head.append(el("span", "tx-callout-label", label));
    if (agent) head.append(el("span", "tx-callout-agent", agent.name));
    head.append(el("time", "tx-time mono", time(event.at)));
    node.append(head);
    if (event.preview) node.append(el("p", "tx-callout-text", event.preview));
    return node;
  }

  compactEntry(event, tone) {
    const node = el("article", "tx-entry tx-row");
    const kind = tone === "observation" ? event.stage_label || event.label : KIND_LABELS[tone];
    const agent = this.props.agents[event.agent_id];
    node.append(el("span", "tx-row-kind", kind));
    if (agent) node.append(el("span", "tx-row-agent", agent.name));
    node.append(el("span", "tx-row-text", (event.preview || "").replace(/\s+/g, " ")), el("time", "tx-time mono", time(event.at)));
    return node;
  }

  onIntersect(entries) {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      this.observer.unobserve(entry.target);
      this.lateObserver.unobserve(entry.target);
      this.loadBody(entry.target);
    }
  }

  async loadBody(node) {
    const id = node.dataset.id;
    const event = this.props.events.find((candidate) => candidate.id === id);
    if (!event || this.loaded.has(id)) return;
    this.loaded.add(id);
    this.measure(node);
    try {
      const detail = await this.handlers.loadDetail(event);
      const content = detail?.data?.content;
      if (typeof content !== "string" || content === event.preview) return;
      node.querySelector(".tx-body").replaceChildren(renderMarkdown(content));
      this.measure(node);
    } catch (error) {
      this.loaded.delete(id);
      delete node.dataset.watch;
      failure(error);
    }
  }

  measure(node) {
    const body = node.querySelector(".tx-body");
    requestAnimationFrame(() => {
      node.querySelector(".tx-more").hidden = !node.classList.contains("tx-expanded")
        && body.scrollHeight <= body.clientHeight + 1;
    });
  }

  onGlobalKey(event) {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable='true']")) return;
    if (!this.root.offsetParent) return;
    event.preventDefault();
    this.search.focus();
  }
}
