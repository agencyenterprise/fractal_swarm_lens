import { el, button, api, failure, formatNumber, color, eventTone, stageOf, speakerName, avatar } from "./ui.js";
import { renderMarkdown } from "./markdown.js";
import { Picker } from "./components.js?v=1";

const CHUNK = 60;

export function installCompare(host) {
  const view = new CompareView(host);
  view.panel = host.registerView({ id: "compare", title: "Compare", onShow: (params) => view.show(params) });
  view.panel.classList.add("cmp");
  return view;
}

// Branch lineage

function branchById(workspace, id) {
  return workspace.branches.find((branch) => branch.id === id);
}

function runOf(workspace, branch) {
  return workspace.runs.find((run) => run.id === branch.run_id);
}

// Each ancestor maps to the last position this branch inherits from it.
function lineage(workspace, branch) {
  const limits = new Map([[branch.id, Infinity]]);
  let limit = Infinity;
  for (let node = branch; node.parent_id; node = branchById(workspace, node.parent_id)) {
    limit = Math.min(limit, node.fork_position);
    limits.set(node.parent_id, limit);
  }
  return limits;
}

function sharedThrough(workspace, left, right) {
  const leftLimits = lineage(workspace, left);
  let shared = 0;
  for (const [id, limit] of lineage(workspace, right))
    if (leftLimits.has(id)) shared = Math.max(shared, Math.min(limit, leftLimits.get(id)));
  return Math.min(shared, left.head, right.head);
}

// Any branch of any run can be compared; the current run's branches come first.
function candidatesFor(workspace, branch) {
  const own = workspace.branches.filter((candidate) => candidate.run_id === branch.run_id);
  return [...own, ...workspace.branches.filter((candidate) => candidate.run_id !== branch.run_id)];
}

function defaultLeft(candidates, right) {
  return candidates.find((candidate) => candidate.id === right.parent_id)
    || candidates.find((candidate) => candidate.id !== right.id);
}

function branchLabel(workspace, branch) {
  const run = runOf(workspace, branch);
  return `${run.name} · ${branch.name}`;
}

function branchOption(workspace, branch) {
  const fork = branch.parent_id ? ` · forked at event ${branch.fork_position}` : "";
  return { value: branch.id, label: branchLabel(workspace, branch), description: `${formatNumber(branch.head)} events${fork}` };
}

// Row alignment

const isShown = (event) => event.kind === "message.created" || eventTone(event) === "intervention";

function segments(events) {
  const result = [];
  for (const event of events) {
    const stage = stageOf(event);
    const last = result.at(-1);
    if (stage && last?.stage === stage) last.events.push(event);
    else result.push({ stage, events: [event] });
  }
  const seen = new Map();
  let loose = 0;
  return result.map((segment) => {
    if (!segment.stage) return { ...segment, key: `#${loose++}` };
    const occurrence = seen.get(segment.stage) || 0;
    seen.set(segment.stage, occurrence + 1);
    return { ...segment, key: `${segment.stage}#${occurrence}` };
  });
}

const messageKey = (events) => events.filter((event) => event.kind === "message.created")
  .map((event) => `${event.agent_name || ""}\u0000${event.preview || ""}`).join("\u0001");

function row(stage, left, right) {
  return { stage, left, right, differs: messageKey(left) !== messageKey(right) };
}

// Stages match by label and occurrence; unstaged events match by order. Unmatched segments get a one-sided row.
function align(leftEvents, rightEvents) {
  const left = segments(leftEvents), right = segments(rightEvents);
  const inLeft = new Map(left.map((segment, index) => [segment.key, index]));
  const inRight = new Map(right.map((segment, index) => [segment.key, index]));
  const rows = [];
  let i = 0, j = 0;
  while (i < left.length || j < right.length) {
    const a = left[i], b = right[j];
    if (a && b && a.key === b.key) {
      rows.push(row(a.stage, a.events, b.events));
      i++;
      j++;
    } else if (b && !(inLeft.get(b.key) >= i) && (!a || inRight.get(a.key) >= j)) {
      rows.push(row(b.stage, [], b.events));
      j++;
    } else {
      rows.push(row(a.stage, a.events, []));
      i++;
    }
  }
  return rows;
}

function agentsOf(events) {
  return Object.fromEntries(events.filter((event) => event.kind === "agent.added")
    .map((event) => [event.agent_id, { id: event.agent_id, name: event.agent_name, model: event.model }]));
}

function diffBadges(diff) {
  const badges = [];
  const count = (entry) => entry.added.length + entry.removed.length + entry.changed.length;
  const plural = (n, word) => `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;
  if (count(diff.agents)) badges.push([plural(count(diff.agents), "agent") + " changed", "warn"]);
  if (diff.environment.before.goal !== diff.environment.after.goal) badges.push(["Goal changed", "warn"]);
  else if (JSON.stringify(diff.environment.before) !== JSON.stringify(diff.environment.after))
    badges.push(["Environment changed", "warn"]);
  if (count(diff.channels)) badges.push([plural(count(diff.channels), "channel") + " changed", ""]);
  if (count(diff.messages)) badges.push([plural(count(diff.messages), "message") + " differ", ""]);
  if (count(diff.tools)) badges.push([plural(count(diff.tools), "tool call") + " differ", ""]);
  return badges.length ? badges : [["Same state", ""]];
}

class CompareView {
  constructor(host) {
    this.host = host;
    this.renderKey = null;
    this.generation = 0;
    this.pending = new WeakMap();
    this.left = this.picker("Left branch");
    this.right = this.picker("Right branch");
    this.left.onchange = () => this.choose(this.left.value, this.right.value, "left");
    this.right.onchange = () => this.choose(this.left.value, this.right.value, "right");
  }

  picker(label) {
    return new Picker({ label, compact: true, hideLabel: true, placeholder: "Choose a branch" });
  }

  show(params = {}) {
    const { workspace, branch } = this.host.context();
    if (!branch) return;
    this.workspace = workspace;
    this.candidates = candidatesFor(workspace, branch);
    const valid = (id) => this.candidates.find((candidate) => candidate.id === id);
    const right = valid(params.right) || branch;
    const left = valid(params.left) && params.left !== right.id ? valid(params.left) : defaultLeft(this.candidates, right);
    if (!left) return this.renderEmpty();
    return this.render(left, right);
  }

  choose(leftId, rightId, changed) {
    if (leftId === rightId) [leftId, rightId] = changed === "left" ? [leftId, this.current.left.id] : [this.current.right.id, rightId];
    const find = (id) => branchById(this.workspace, id);
    this.render(find(leftId), find(rightId)).catch(failure);
  }

  renderEmpty() {
    this.renderKey = null;
    this.generation++;
    this.panel.replaceChildren(el("p", "empty", "Fork the run to compare branches."));
  }

  async render(left, right) {
    const key = [left.id, left.head, right.id, right.head].join("|");
    this.host.setViewParams({ left: left.id, right: right.id });
    if (key === this.renderKey) return;
    this.renderKey = key;
    const generation = ++this.generation;
    this.current = { left, right };
    this.scroller = el("div", "cmp-scroll");
    this.scroller.dataset.viewScroll = "";
    this.body = el("div", "cmp-inner");
    this.body.append(this.bar(left, right));
    this.scroller.append(this.body);
    this.panel.replaceChildren(this.scroller);
    this.left.mount();
    this.right.mount();
    this.detailObserver?.disconnect();
    this.detailObserver = new IntersectionObserver((entries) => this.loadVisible(entries),
      { root: this.scroller, rootMargin: "600px 0px" });

    const [leftTimeline, rightTimeline] = await Promise.all([this.host.loadTimeline(left.id), this.host.loadTimeline(right.id)]);
    if (generation !== this.generation) return;
    const sides = { left: this.side(left, leftTimeline.events), right: this.side(right, rightTimeline.events) };
    const shared = sharedThrough(this.workspace, left, right);
    const badges = el("div", "cmp-badges");
    this.body.append(badges, this.outcome(sides), this.transcript(sides, shared));
    if (left.run_id === right.run_id) this.fillBadges(badges, left, right, shared, generation).catch(failure);
    else badges.remove();
  }

  side(branch, events) {
    return { branch, agents: agentsOf(events), events: events.filter(isShown) };
  }

  bar(left, right) {
    const bar = el("div", "cmp-bar cmp-grid");
    const options = this.candidates.map((candidate) => branchOption(this.workspace, candidate));
    for (const [picker, branch] of [[this.left, left], [this.right, right]]) {
      picker.setOptions(options);
      picker.value = branch.id;
    }
    const swap = button("⇄", () => this.render(right, left).catch(failure), "icon ghost cmp-swap");
    swap.setAttribute("aria-label", "Swap sides");
    swap.title = "Swap sides";
    bar.append(this.left.root, swap, this.right.root);
    return bar;
  }

  async fillBadges(container, left, right, shared, generation) {
    const leftCursor = lineage(this.workspace, right).has(left.id) ? shared : Math.min(left.head, right.head);
    const query = new URLSearchParams({ left: left.id, right: right.id, left_cursor: leftCursor, right_cursor: right.head });
    const diff = await api(`/compare?${query}`);
    if (generation !== this.generation) return;
    container.replaceChildren(...diffBadges(diff).map(([text, tone]) => el("span", `badge ${tone}`.trim(), text)));
  }

  outcome(sides) {
    const section = el("section", "cmp-section");
    const grid = el("div", "cmp-grid cmp-outcome");
    for (const [column, side] of [[1, sides.left], [3, sides.right]]) {
      const last = side.events.findLast((event) => event.kind === "message.created");
      const cell = el("div", "cmp-cell");
      cell.style.gridColumn = column;
      cell.append(last ? this.message(side, last, { eager: true }) : el("p", "muted", "No messages"));
      grid.append(cell);
    }
    section.append(el("h2", "section-title", "Outcome"), grid);
    return section;
  }

  transcript(sides, shared) {
    const section = el("section", "cmp-section");
    const head = el("div", "cmp-section-head");
    head.append(el("h2", "section-title", "Transcript"));
    const sharedOf = (side) => side.events.filter((event) => event.position <= shared);
    const afterOf = (side) => side.events.filter((event) => event.position > shared);
    const divergent = align(afterOf(sides.left), afterOf(sides.right));
    const sharedRows = shared ? align(sharedOf(sides.right), sharedOf(sides.right)) : [];
    this.rows = { shared: sharedRows, divergent, expanded: false, sides, through: shared };
    const first = divergent.findIndex((candidate) => candidate.differs);
    this.firstDifference = first < 0 ? null : divergent[first];
    if (this.firstDifference) head.append(button("Jump to first difference", () => this.jumpToFirstDifference(), "ghost cmp-jump"));
    this.list = el("div", "cmp-rows");
    section.append(head, this.list);
    this.renderRows();
    return section;
  }

  visibleRows() {
    const { shared, divergent, expanded } = this.rows;
    return [...(shared.length ? [{ type: "shared" }] : []), ...(expanded ? shared : []), ...divergent];
  }

  renderRows() {
    this.sentinelObserver?.disconnect();
    this.list.replaceChildren();
    this.ordered = this.visibleRows();
    this.rendered = 0;
    this.sentinel = el("div", "cmp-sentinel");
    this.sentinelObserver = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) this.renderMore();
    }, { root: this.scroller, rootMargin: "800px 0px" });
    this.renderMore();
  }

  renderMore(until = 0) {
    const end = Math.min(this.ordered.length, Math.max(this.rendered + CHUNK, until + 1));
    const fragment = document.createDocumentFragment();
    for (; this.rendered < end; this.rendered++) {
      const item = this.ordered[this.rendered];
      fragment.append(item.type === "shared" ? this.sharedRow() : this.transcriptRow(item));
    }
    this.sentinel.remove();
    this.list.append(fragment);
    if (this.rendered < this.ordered.length) {
      this.list.append(this.sentinel);
      this.sentinelObserver.observe(this.sentinel);
    } else this.sentinelObserver.disconnect();
  }

  sharedRow() {
    const node = el("div", "cmp-shared");
    const toggle = button(this.rows.expanded ? "Hide" : "Show", () => {
      this.rows.expanded = !this.rows.expanded;
      this.renderRows();
    }, "ghost");
    toggle.setAttribute("aria-expanded", String(this.rows.expanded));
    node.append(el("span", "", `Same history through event ${formatNumber(this.rows.through)}`), toggle);
    return node;
  }

  transcriptRow(item) {
    const node = el("div", "cmp-row cmp-grid");
    const onlyCallouts = [...item.left, ...item.right].every((event) => event.kind !== "message.created");
    if ((item.stage && !onlyCallouts) || item === this.firstDifference) {
      const label = el("div", "cmp-stage", item.stage || "");
      if (item === this.firstDifference) label.append(el("span", "badge accent", "First difference"));
      node.append(label);
    }
    if (item === this.firstDifference) this.firstDifferenceNode = node;
    for (const [column, side, events] of [[1, this.rows.sides.left, item.left], [3, this.rows.sides.right, item.right]]) {
      const cell = el("div", "cmp-cell");
      cell.style.gridColumn = column;
      cell.append(...events.map((event) => event.kind === "message.created" ? this.message(side, event) : this.callout(side, event)));
      node.append(cell);
    }
    return node;
  }

  jumpToFirstDifference() {
    const index = this.ordered.indexOf(this.firstDifference);
    if (index >= this.rendered) this.renderMore(index);
    this.firstDifferenceNode.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  positionButton(side, event) {
    const open = button(`#${event.position}`, () => this.host.openTimeline(side.branch.id, event.position), "ghost cmp-position");
    open.setAttribute("aria-label", `Open event ${event.position} in Timeline`);
    return open;
  }

  message(side, event, { eager = false } = {}) {
    const agent = side.agents[event.agent_id];
    const node = el("article", "cmp-message");
    if (agent) node.style.setProperty("--agent", color(event.agent_id, side.agents));
    const head = el("div", "cmp-message-head");
    head.append(avatar(agent || { name: speakerName(event, side.agents) }),
      el("span", "cmp-speaker", event.agent_name || speakerName(event, side.agents)), this.positionButton(side, event));
    const body = el("div", "md cmp-clamp");
    body.append(renderMarkdown(event.preview));
    const more = button("Show more", () => {
      const open = body.classList.toggle("open");
      more.textContent = open ? "Show less" : "Show more";
    }, "ghost cmp-more");
    more.hidden = true;
    node.append(head, body, more);
    this.pending.set(node, { side, event, body, more });
    if (eager) this.loadMessage(node);
    else this.detailObserver.observe(node);
    return node;
  }

  loadVisible(entries) {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      this.detailObserver.unobserve(entry.target);
      this.loadMessage(entry.target);
    }
  }

  async loadMessage(node) {
    const { side, event, body, more } = this.pending.get(node);
    this.pending.delete(node);
    try {
      const detail = await this.host.loadDetail(side.branch.id, event);
      body.replaceChildren(renderMarkdown(detail.data?.content ?? event.preview));
    } catch (error) {
      failure(error);
    }
    requestAnimationFrame(() => {
      const clipped = body.scrollHeight > body.clientHeight + 2;
      body.classList.toggle("clipped", clipped);
      more.hidden = !clipped;
    });
  }

  callout(side, event) {
    const node = el("div", "cmp-callout warn");
    const head = el("div", "cmp-callout-head");
    head.append(el("span", "cmp-callout-label", event.stage_label || event.label), this.positionButton(side, event));
    node.append(head);
    if (event.preview) node.append(el("p", "cmp-callout-text", event.preview));
    return node;
  }
}
