import { el, button, avatar, color, eventTone, stageOf, speakerName, time, formatNumber, failure } from "./ui.js";
import { renderMarkdownInto } from "./markdown.js";

const PAGE = 150;
const LATER_PAGE = 50;
const CALLOUT_TONES = new Set(["intervention"]);
const KINDS = [
  ["message", "Messages"],
  ["intervention", "Changes"],
  ["tool", "Tools"],
  ["memory", "Memory"],
  ["observation", "Observations"],
];
const DEFAULT_KINDS = ["message", "intervention"];
const KINDS_KEY = "swarm-lens:transcript-kinds";
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

// Short button text for the kinds shown, e.g. "Messages, Changes" or "3 kinds".
export function kindsSummary(kinds) {
  if (kinds.size === KINDS.length) return "All";
  if (!kinds.size) return "None";
  if (kinds.size > 2) return `${kinds.size} kinds`;
  return KINDS.filter(([kind]) => kinds.has(kind)).map(([, label]) => label).join(", ");
}

// Previews of serialized data carry escaped newlines; a one-line row reads them as spaces.
function oneLine(text) {
  return (text || "").replace(/(?:\\n|\s)+/g, " ").trim();
}

function loadKinds() {
  try {
    const saved = JSON.parse(localStorage.getItem(KINDS_KEY));
    if (Array.isArray(saved)) return new Set(saved.filter((kind) => KINDS.some(([known]) => known === kind)));
  } catch { /* Storage unavailable or corrupt; fall back to the defaults. */ }
  return new Set(DEFAULT_KINDS);
}

function saveKinds(kinds) {
  try { localStorage.setItem(KINDS_KEY, JSON.stringify([...kinds])); } catch { /* The choice lasts for this visit. */ }
}

// Events the transcript shows for the given filters, oldest first.
export function visibleEvents(events, { agentId, kinds, query, agents }) {
  const needle = query.trim().toLowerCase();
  return events.filter((event) =>
    (!agentId || event.agent_id === agentId)
    && kinds.has(eventTone(event))
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
    this.kinds = loadKinds();
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
    document.addEventListener("pointerdown", (event) => {
      if (!this.picker.contains(event.target)) this.closeKinds();
    });
  }

  buildShell() {
    this.toolbar = el("div", "tx-toolbar");
    this.buildKindPicker();
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
    this.toolbar.append(this.picker, this.search, this.chip, this.count);
    this.list = el("div", "tx-list");
    this.earlier = button("Show earlier", () => this.showMore("before", PAGE), "ghost tx-page");
    this.later = button("Show later", () => this.showMore("after", LATER_PAGE), "ghost tx-page");
    this.empty = el("p", "empty");
    this.root.replaceChildren(this.toolbar, this.list);
  }

  // A "Show" button opening checkboxes per event kind; clicking a kind's name shows only that kind.
  buildKindPicker() {
    this.picker = el("div", "tx-show");
    this.showButton = button("", () => this.toggleKinds(), "tx-show-button");
    this.showButton.setAttribute("aria-haspopup", "true");
    this.showButton.setAttribute("aria-expanded", "false");
    this.showSummary = el("span", "tx-show-summary");
    this.showButton.append(el("span", "tx-show-label", "Show"), this.showSummary);
    this.kindPanel = el("div", "menu tx-kinds");
    this.kindPanel.setAttribute("role", "group");
    this.kindPanel.setAttribute("aria-label", "Event kinds");
    this.kindPanel.hidden = true;
    this.kindBoxes = new Map(KINDS.map(([kind, label]) => [kind, this.kindRow(kind, label)]));
    this.picker.append(this.showButton, this.kindPanel);
    this.picker.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || this.kindPanel.hidden) return;
      event.stopPropagation();
      this.closeKinds();
      this.showButton.focus();
    });
    this.syncKinds();
  }

  kindRow(kind, label) {
    const row = el("div", "tx-kind");
    const box = el("input");
    box.type = "checkbox";
    box.setAttribute("aria-label", label);
    box.addEventListener("change", () => {
      const kinds = new Set(this.kinds);
      if (box.checked) kinds.add(kind);
      else kinds.delete(kind);
      this.setKinds(kinds);
    });
    const only = button(label, () => this.setKinds(new Set([kind])), "tx-kind-only");
    only.setAttribute("aria-label", `Show only ${label}`);
    only.append(el("span", "hint", "Only"));
    row.append(box, only);
    this.kindPanel.append(row);
    return box;
  }

  toggleKinds() {
    if (!this.kindPanel.hidden) return this.closeKinds();
    this.kindPanel.hidden = false;
    this.showButton.setAttribute("aria-expanded", "true");
    this.kindPanel.querySelector("input").focus();
  }

  closeKinds() {
    this.kindPanel.hidden = true;
    this.showButton.setAttribute("aria-expanded", "false");
  }

  setKinds(kinds) {
    saveKinds(kinds);
    this.setFilter({ kinds });
    this.syncKinds();
  }

  syncKinds() {
    for (const [kind, box] of this.kindBoxes) box.checked = this.kinds.has(kind);
    this.showSummary.textContent = kindsSummary(this.kinds);
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
    const { events, agents, cursor, selectedId, agentId, comments } = this.props;
    const matching = visibleEvents(events, { agentId, kinds: this.kinds, query: this.query, agents });
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
      this.setCommentBadge(node, event, comments?.openCount(event.id) ?? 0);
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

  setCommentBadge(node, event, count) {
    let badge = node.querySelector(".tx-comments");
    if (!count) return badge?.remove();
    if (!badge) {
      badge = button("", (click) => {
        click.stopPropagation();
        this.handlers.onSelect(event);
      }, "tx-comments");
      node.querySelector(".tx-time").before(badge);
    }
    badge.textContent = formatNumber(count);
    badge.setAttribute("aria-label", `${count} open comment${count === 1 ? "" : "s"}`);
  }

  renderToolbar(total, agents, agentId) {
    const noun = this.kinds.size === 1 && this.kinds.has("message") ? "message" : "event";
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
    const body = el("div", "tx-body");
    renderMarkdownInto(body, event.preview).catch(failure);
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
    const label = interventionLabel(event);
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
    node.append(el("span", "tx-row-text", oneLine(event.preview)), el("time", "tx-time mono", time(event.at)));
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
      await renderMarkdownInto(node.querySelector(".tx-body"), typeof content === "string" ? content : event.preview);
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
