import { el, button, svg, time, color, eventTone, stageOf, speakerName, avatar } from "./ui.js";
import { timelineTime, gapDuration } from "./timeline-time.js";

const LABEL_WIDTH = 150, ROW = 34, RULER = 40, EDGE = 24, EVENT_SPACE = 22, MAX_PX_PER_SECOND = 400;
const STORAGE_KEY = "swarm-lens.timeline.view";
const VIEW_OPTIONS = [
  ["message", "Messages", true],
  ["risk", "Risk & observations", true],
  ["intervention", "Changes", true],
  ["tool", "Tools", true],
  ["memory", "Memory", false],
  ["trace", "Model inputs", false],
  ["connections", "Connections", true],
  ["compact", "Compact gaps", true],
];
// Which View toggle shows each tone; setup events (`state`) have no lane marker.
const TOGGLE_FOR_TONE = { message: "message", risk: "risk", observation: "risk", intervention: "intervention",
  tool: "tool", memory: "memory", trace: "trace" };
const TICK_MINUTES = [1 / 60, 1 / 30, 1 / 12, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 240, 720, 1440];
const MIN_TICK_SPACING = 90;
const ICONS = {
  prev: ["M10 3.5 5.5 8l4.5 4.5", false],
  next: ["M6 3.5 10.5 8 6 12.5", false],
  play: ["M5 3.2v9.6L12.6 8z", true],
  pause: ["M4.5 3.5h2.6v9H4.5zM8.9 3.5h2.6v9H8.9z", true],
};

const isResume = (event) => event.resume_point && !event.resume_point.replay_reason
  && event.resume_point.next_task < event.resume_point.total_tasks;
const toneOf = (event) => (isResume(event) ? "resume" : eventTone(event));
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function flagText(event, tone) {
  if (tone === "resume") return `Task ${event.resume_point.next_task + 1}`;
  if (tone === "risk") return event.stage_label || event.label;
  if (tone === "intervention") return event.stage_label || event.preview?.split("\n")[0] || event.label;
  return "";
}

function loadView() {
  const view = Object.fromEntries(VIEW_OPTIONS.map(([key, , on]) => [key, on]));
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    for (const key of Object.keys(view)) if (typeof saved[key] === "boolean") view[key] = saved[key];
  } catch {
    // Storage can be unavailable (private mode, tests); defaults apply.
  }
  return view;
}

function saveView(view) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(view));
  } catch {
    // Not persisting a display preference is harmless.
  }
}

function icon([d, filled]) {
  const node = svg("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" });
  node.append(svg("path", filled ? { d, fill: "currentColor" }
    : { d, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  return node;
}

function iconButton(label, glyph, action) {
  const node = button("", action, "icon ghost");
  node.setAttribute("aria-label", label);
  node.append(icon(glyph));
  return node;
}

// Index of the last item whose key is <= value, or -1.
function lastAtOrBefore(items, value, key) {
  let low = 0, high = items.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (key(items[middle]) <= value) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

function collectAgents(events) {
  const agents = {};
  for (const event of events) {
    if (event.kind === "agent.added" || event.kind === "agent.updated")
      agents[event.agent_id] = { id: event.agent_id, name: event.agent_name || event.preview, model: event.model };
  }
  for (const event of events)
    if (event.agent_id && !agents[event.agent_id])
      agents[event.agent_id] = { id: event.agent_id, name: event.agent_name || event.agent_id, model: event.model };
  return agents;
}

function collectChannels(events) {
  const channels = new Map();
  for (const event of events)
    if (event.kind === "channel.created")
      channels.set(event.entity_id, { id: event.entity_id, name: event.channel_name || event.preview });
  for (const event of events)
    if (event.channel_id && !channels.has(event.channel_id))
      channels.set(event.channel_id, { id: event.channel_id, name: event.channel_id });
  return [...channels.values()];
}

// Consecutive events that share a stage form one band; flagged moments do not split bands.
function stageBands(ordered, tones) {
  const bands = [];
  for (const event of ordered) {
    const label = stageOf(event);
    if (!label || ["risk", "intervention", "resume"].includes(tones.get(event.id))) continue;
    const last = bands.at(-1);
    if (last?.label === label) last.last = event;
    else bands.push({ label, first: event, last: event });
  }
  return bands;
}

// Neighbouring stages often repeat a prefix ("Debate round 6", "Debate round 7"); a narrow band keeps
// the distinct tail plus the last shared word ("Round 6").
function compactStage(label, neighbour) {
  const words = label.split(" "), other = neighbour?.split(" ") || [];
  let shared = 0;
  while (shared < words.length - 1 && words[shared] === other[shared]) shared++;
  if (shared < 2) return label;
  const tail = words.slice(shared - 1).join(" ");
  return tail[0].toUpperCase() + tail.slice(1);
}

const fitsLabel = (text, width) => text.length * 6 + 8 <= width;

export class EventTimeline {
  constructor(root, handlers) {
    this.root = root;
    this.handlers = handlers;
    this.events = [];
    this.ordered = [];
    this.shown = [];
    this.xs = [];
    this.lanes = [];
    this.agents = {};
    this.branch = null;
    this.cursor = 0;
    this.selected = null;
    this.scale = 1;
    this.scaleMode = "auto";
    this.view = loadView();
    this.nodes = new Map();
    this.pool = [];
    this.layoutVersion = 0;
    this.build();
  }

  build() {
    this.root.classList.add("tl");
    this.root.replaceChildren(this.buildHeader(), this.buildViewport(), this.buildTooltip(), this.buildMenu());
    this.root.addEventListener("keydown", (event) => this.onKey(event));
    new ResizeObserver(() => {
      if (this.viewport.clientWidth !== this.measuredWidth) this.layout();
    }).observe(this.viewport);
  }

  buildHeader() {
    const header = el("header", "tl-header");
    const transport = el("div", "tl-transport");
    this.playButton = iconButton("Play", ICONS.play, () => this.handlers.onPlay());
    transport.append(iconButton("Previous event", ICONS.prev, () => this.step(-1)), this.playButton,
      iconButton("Next event", ICONS.next, () => this.step(1)));
    this.positionText = el("span", "tl-position mono");
    this.timeText = el("span", "tl-time mono");
    this.stageText = el("span", "tl-stage");
    this.viewButton = button("View", () => this.toggleMenu(), "ghost tl-view-button");
    this.viewButton.setAttribute("aria-haspopup", "true");
    this.viewButton.setAttribute("aria-expanded", "false");
    header.append(transport, this.positionText, this.timeText, this.stageText, el("span", "tl-spacer"), this.viewButton);
    return header;
  }

  buildViewport() {
    this.viewport = el("div", "tl-viewport");
    this.viewport.tabIndex = 0;
    this.viewport.dataset.viewScroll = "";
    this.viewport.setAttribute("aria-label", "Event lanes");
    this.scene = el("div", "tl-scene");
    this.bandLayer = el("div", "tl-bands");
    this.ruler = el("div", "tl-ruler");
    this.rulerTrack = el("div", "tl-ruler-track");
    this.handle = el("div", "tl-handle");
    this.handle.setAttribute("aria-hidden", "true");
    this.ruler.append(el("div", "tl-corner"), this.rulerTrack, this.handle);
    this.laneLayer = el("div", "tl-lanes");
    this.linkLayer = svg("svg", { class: "tl-links", "aria-hidden": "true" });
    this.markerLayer = el("div", "tl-markers");
    this.forkLine = el("div", "tl-fork-line");
    this.playhead = el("div", "tl-playhead");
    this.scene.append(this.bandLayer, this.ruler, this.laneLayer, this.linkLayer, this.markerLayer, this.forkLine, this.playhead);
    this.viewport.append(this.scene);

    this.viewport.addEventListener("scroll", () => {
      this.hideTooltip();
      if (!this.frame) this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.draw();
      });
    });
    this.viewport.addEventListener("click", (event) => this.onLaneClick(event));
    this.viewport.addEventListener("contextmenu", (event) => this.onLaneContext(event));
    this.viewport.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
    this.ruler.addEventListener("pointerdown", (event) => this.startScrub(event));
    this.markerLayer.addEventListener("click", (event) => this.onMarkerClick(event));
    this.markerLayer.addEventListener("contextmenu", (event) => this.onMarkerContext(event));
    this.markerLayer.addEventListener("keydown", (event) => this.onMarkerKey(event));
    this.markerLayer.addEventListener("pointerover", (event) => this.onMarkerHover(event));
    this.markerLayer.addEventListener("pointerout", (event) => {
      if (!event.relatedTarget?.closest?.(".tl-marker")) this.hideTooltip();
    });
    return this.viewport;
  }

  buildTooltip() {
    this.tooltip = el("div", "tl-tooltip");
    this.tooltip.hidden = true;
    this.tooltip.setAttribute("aria-hidden", "true");
    return this.tooltip;
  }

  buildMenu() {
    this.menu = el("div", "menu tl-menu");
    this.menu.hidden = true;
    this.menu.setAttribute("role", "group");
    this.menu.setAttribute("aria-label", "Timeline view");
    for (const [key, label] of VIEW_OPTIONS) {
      const row = el("label", "tl-check");
      const input = el("input");
      input.type = "checkbox";
      input.checked = this.view[key];
      input.addEventListener("change", () => this.setOption(key, input.checked));
      row.append(input, el("span", "", label));
      if (key === "connections") this.connectionsOption = row;
      this.menu.append(row);
    }
    const zoom = el("div", "tl-zoom");
    const zoomOut = button("−", () => this.zoomBy(1 / 1.8), "");
    const zoomIn = button("+", () => this.zoomBy(1.8), "");
    zoomOut.setAttribute("aria-label", "Zoom out");
    zoomIn.setAttribute("aria-label", "Zoom in");
    zoom.append(zoomOut, zoomIn, button("Fit", () => this.fit(), "tl-fit"));
    this.menu.append(el("hr"), zoom);
    this.dismissMenu = (event) => {
      if (!this.menu.contains(event.target) && !this.viewButton.contains(event.target)) this.closeMenu();
    };
    return this.menu;
  }

  toggleMenu() {
    if (!this.menu.hidden) return this.closeMenu();
    const rect = this.viewButton.getBoundingClientRect();
    this.menu.style.top = `${rect.bottom + 4}px`;
    this.menu.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
    this.menu.hidden = false;
    this.viewButton.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", this.dismissMenu, true);
  }

  closeMenu() {
    this.menu.hidden = true;
    this.viewButton.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", this.dismissMenu, true);
  }

  setOption(key, on) {
    this.view[key] = on;
    saveView(this.view);
    if (key === "connections") return this.drawLinks();
    if (key === "compact") {
      this.scaleMode = "auto";
      this.prepare();
    }
    this.refresh();
    this.setCursor(this.cursor, true);
  }

  setData(events, branch) {
    const branchChanged = branch?.id !== this.branch?.id;
    this.events = events;
    this.branch = branch;
    this.byId = new Map(events.map((event) => [event.id, event]));
    this.tones = new Map(events.map((event) => [event.id, toneOf(event)]));
    this.agents = collectAgents(events);
    this.channels = collectChannels(events);
    if (branchChanged) {
      this.scaleMode = "auto";
      this.selected = null;
      this.viewport.scrollLeft = 0;
      this.viewport.scrollTop = 0;
    }
    this.prepare();
    this.refresh();
    this.updateHeader();
  }

  // Display clock and range; depends on events and the compact-gaps option.
  prepare() {
    this.clock = timelineTime(this.events, this.view.compact);
    this.ordered = this.clock.ordered;
    const content = this.events.filter((event) => this.tones.get(event.id) !== "state");
    const times = (content.length ? content : this.events).map((event) => this.clock.at(event));
    this.start = times.length ? Math.min(...times) : 0;
    this.end = Math.max(this.start + 1000, ...this.events.map((event) => this.clock.at(event)));
    this.bands = stageBands(this.ordered, this.tones);
  }

  isShown(event) {
    const tone = this.tones.get(event.id);
    return tone === "resume" || !!this.view[TOGGLE_FOR_TONE[tone]];
  }

  laneFor(event) {
    if (isResume(event)) return "__system";
    if (event.agent_id) return event.agent_id;
    return event.kind === "message.created" ? "__human" : "__system";
  }

  // Visible events and lanes; depends on the View toggles.
  refresh() {
    const shown = this.ordered.filter((event) => this.isShown(event));
    // One channel receives every message, so its lane and the links into it carry no information.
    const routed = this.channels.length > 1;
    this.connectionsOption.hidden = !routed;
    this.lanes = [
      ...(routed ? this.channels.map((channel) => ({ ...channel, kind: "channel" })) : []),
      ...Object.values(this.agents).map((agent) => ({ ...agent, kind: "agent" })),
    ];
    if (this.events.some((event) => event.kind === "message.created" && !event.agent_id))
      this.lanes.push({ id: "__human", name: "Human", kind: "human" });
    if (shown.some((event) => this.laneFor(event) === "__system"))
      this.lanes.push({ id: "__system", name: "System", kind: "system" });
    this.laneIndex = new Map(this.lanes.map((lane, index) => [lane.id, index]));
    this.shown = shown.filter((event) => this.laneIndex.has(this.laneFor(event)));
    this.layout();
  }

  x(event) {
    return this.xAt(this.clock.at(event));
  }

  xAt(at) {
    return LABEL_WIDTH + EDGE + ((at - this.start) / 60000) * this.scale;
  }

  laneY(laneId) {
    return RULER + this.laneIndex.get(laneId) * ROW + ROW / 2;
  }

  durationMinutes() {
    return (this.end - this.start) / 60000;
  }

  fitScale() {
    return Math.max(1, this.viewport.clientWidth - LABEL_WIDTH - EDGE * 2) / this.durationMinutes();
  }

  autoScale() {
    const perLane = new Map();
    for (const event of this.shown) {
      const lane = this.laneFor(event);
      perLane.set(lane, (perLane.get(lane) || 0) + 1);
    }
    // Small histories fit the view; dense lanes get room to scroll.
    const readable = ((Math.max(1, ...perLane.values()) - 1) * EVENT_SPACE) / this.durationMinutes();
    return Math.max(this.fitScale(), readable);
  }

  layout() {
    this.measuredWidth = this.viewport.clientWidth;
    if (!this.clock || !this.measuredWidth) return;
    this.layoutVersion++;
    if (this.scaleMode === "fit") this.scale = this.fitScale();
    else if (this.scaleMode === "auto") this.scale = this.autoScale();
    this.width = Math.max(this.measuredWidth, Math.round(LABEL_WIDTH + this.durationMinutes() * this.scale + EDGE * 2));
    this.height = RULER + this.lanes.length * ROW;
    this.scene.style.width = `${this.width}px`;
    this.scene.style.height = `${this.height}px`;
    this.linkLayer.setAttribute("width", this.width);
    this.linkLayer.setAttribute("height", this.height);
    this.xs = this.shown.map((event) => this.x(event));
    this.offsets = this.markerOffsets();
    this.ticks = this.timeTicks();
    this.buildLanes();
    this.placeFork();
    this.placePlayhead();
    this.draw();
  }

  // Markers closer than a few pixels in one lane fan out vertically.
  markerOffsets() {
    const offsets = new Map(), last = new Map();
    this.shown.forEach((event, index) => {
      const lane = this.laneFor(event), x = this.xs[index], previous = last.get(lane);
      const step = previous && x - previous.x < 7 ? (previous.step + 1) % 3 : 0;
      last.set(lane, { x, step });
      offsets.set(event.id, [0, -6, 6][step]);
    });
    return offsets;
  }

  timeTicks() {
    const minutes = TICK_MINUTES.find((value) => value * this.scale >= MIN_TICK_SPACING)
      || Math.ceil(MIN_TICK_SPACING / this.scale / 1440) * 1440;
    const gapCenters = this.clock.gaps.map((gap) => this.xAt((gap.start + gap.end) / 2));
    const ticks = [];
    let lastX = -Infinity;
    for (const { at, actual } of this.clock.ticks(minutes * 60000)) {
      const x = this.xAt(at);
      if (at < this.start || x - lastX < MIN_TICK_SPACING || gapCenters.some((center) => Math.abs(x - center) < 60)) continue;
      ticks.push({ x, label: time(actual).slice(0, minutes < 1 ? 8 : 5) });
      lastX = x;
    }
    return ticks;
  }

  buildLanes() {
    this.laneLayer.replaceChildren(...this.lanes.map((lane) => {
      const row = el("div", `tl-lane is-${lane.kind}`);
      row.append(this.laneLabel(lane));
      return row;
    }));
  }

  laneLabel(lane) {
    const name = el("span", "tl-lane-name", lane.name);
    if (lane.kind === "agent") {
      const label = button("", () => this.handlers.onAgent(lane.id), "tl-label");
      const face = avatar(lane, "avatar tl-avatar");
      face.style.setProperty("--c", color(lane.id, this.agents));
      label.append(face, name);
      label.title = lane.name;
      return label;
    }
    const label = el("div", "tl-label");
    label.append(lane.kind === "channel" ? el("span", "avatar tl-avatar", "#") : avatar(lane, "avatar tl-avatar"), name);
    label.title = lane.name;
    return label;
  }

  forkX() {
    if (!this.branch?.parent_id) return null;
    const index = lastAtOrBefore(this.ordered, this.branch.fork_position, (event) => event.position);
    return index < 0 ? this.xAt(this.start) : this.x(this.ordered[index]);
  }

  placeFork() {
    const x = this.forkX();
    this.forkLine.hidden = x === null;
    if (x !== null) this.forkLine.style.left = `${x}px`;
  }

  cursorX() {
    const index = lastAtOrBefore(this.ordered, this.cursor, (event) => event.position);
    return index < 0 ? LABEL_WIDTH + EDGE : this.x(this.ordered[index]);
  }

  placePlayhead() {
    if (!this.clock) return;
    const x = `${this.cursorX()}px`;
    this.playhead.style.left = x;
    this.handle.style.left = x;
  }

  visibleRange() {
    const left = this.viewport.scrollLeft + LABEL_WIDTH - 40;
    const right = this.viewport.scrollLeft + this.viewport.clientWidth + 80;
    return { left, right };
  }

  draw() {
    if (!this.clock || !this.measuredWidth) return;
    const range = this.visibleRange();
    this.drawRuler(range);
    this.drawBands(range);
    this.drawMarkers(range);
    this.drawLinks();
  }

  bandRects() {
    return this.bands.map((band, index) => {
      const previous = this.bands[index - 1], next = this.bands[index + 1];
      const from = previous ? (this.x(previous.last) + this.x(band.first)) / 2 : this.x(band.first) - EDGE / 2;
      const to = next ? (this.x(band.last) + this.x(next.first)) / 2 : this.x(band.last) + EDGE / 2;
      const short = [previous, next].map((other) => compactStage(band.label, other?.label))
        .reduce((best, text) => (text.length < best.length ? text : best));
      // Always the short form when one exists, so neighbouring bands read alike ("Round 6", "Round 7").
      const text = fitsLabel(short, to - from) ? short : "";
      return { label: band.label, text, from, to, alternate: index % 2 === 1 };
    });
  }

  drawRuler({ left, right }) {
    const items = [];
    for (const band of this.bandRects()) {
      if (band.to < left || band.from > right) continue;
      const label = el("span", `tl-stage-label${band.alternate ? " is-alt" : ""}`, band.text);
      label.style.left = `${band.from}px`;
      label.style.width = `${band.to - band.from}px`;
      label.title = band.label;
      items.push(label);
    }
    for (const tick of this.ticks) {
      if (tick.x < left || tick.x > right) continue;
      const label = el("span", "tl-tick", tick.label);
      label.style.left = `${tick.x}px`;
      items.push(label);
    }
    for (const gap of this.clock.gaps) {
      const x = this.xAt((gap.start + gap.end) / 2);
      if (x < left || x > right) continue;
      const label = el("span", "tl-gap-label", `${gapDuration(gap.duration)} gap`);
      label.style.left = `${x}px`;
      label.title = `${new Date(gap.from).toISOString()} → ${new Date(gap.to).toISOString()}`;
      items.push(label);
    }
    const forkX = this.forkX();
    if (forkX !== null && forkX >= left && forkX <= right) {
      const flag = el("span", "tl-fork-flag", "Forked here");
      flag.style.left = `${forkX}px`;
      items.push(flag);
    }
    this.rulerTrack.replaceChildren(...items);
  }

  drawBands({ left, right }) {
    const items = [];
    for (const band of this.bandRects()) {
      if (!band.alternate || band.to < left || band.from > right) continue;
      const node = el("div", "tl-band");
      node.style.left = `${band.from}px`;
      node.style.width = `${band.to - band.from}px`;
      items.push(node);
    }
    for (const gap of this.clock.gaps) {
      const from = this.xAt(gap.start), to = this.xAt(gap.end);
      if (to < left || from > right) continue;
      const node = el("div", "tl-gap");
      node.style.left = `${from}px`;
      node.style.width = `${Math.max(4, to - from)}px`;
      items.push(node);
    }
    this.bandLayer.replaceChildren(...items);
  }

  windowIndexes({ left, right }) {
    const first = lastAtOrBefore(this.xs, left, (x) => x) + 1;
    const last = lastAtOrBefore(this.xs, right, (x) => x);
    return [first, last];
  }

  drawMarkers(range) {
    const [first, last] = this.windowIndexes(range);
    const keep = new Set(this.shown.slice(first, last + 1).map((event) => event.id));
    for (const [id, node] of this.nodes) {
      if (keep.has(id)) continue;
      node.remove();
      this.nodes.delete(id);
      this.pool.push(node);
    }
    for (let index = first; index <= last; index++) {
      const event = this.shown[index];
      let node = this.nodes.get(event.id);
      if (!node) {
        node = this.pool.pop() || this.createMarker();
        node.layoutVersion = null;
        this.nodes.set(event.id, node);
        this.markerLayer.append(node);
      }
      if (node.layoutVersion !== this.layoutVersion) this.paintMarker(node, event, this.xs[index]);
      this.markerState(node, event);
    }
  }

  createMarker() {
    const node = el("button", "tl-marker");
    node.type = "button";
    node.append(el("span", "tl-shape"), el("span", "tl-flag"));
    return node;
  }

  paintMarker(node, event, x) {
    const tone = this.tones.get(event.id);
    node.layoutVersion = this.layoutVersion;
    node.dataset.eventId = event.id;
    node.dataset.position = event.position;
    node.dataset.tone = tone;
    node.style.left = `${x}px`;
    node.style.top = `${this.laneY(this.laneFor(event)) + this.offsets.get(event.id)}px`;
    node.style.setProperty("--c", event.agent_id ? color(event.agent_id, this.agents) : "var(--text-2)");
    node.lastChild.textContent = flagText(event, tone);
    node.setAttribute("aria-label",
      `${speakerName(event, this.agents)}, ${event.stage_label || event.label}, ${time(event.at)}, event ${event.position}`);
  }

  markerState(node, event) {
    node.classList.toggle("is-future", event.position > this.cursor);
    node.classList.toggle("is-selected", event.id === this.selected);
    node.tabIndex = event.position === this.cursor ? 0 : -1;
  }

  drawLinks() {
    this.linkLayer.replaceChildren();
    if (!this.view.connections || !this.measuredWidth) return;
    const [first, last] = this.windowIndexes(this.visibleRange());
    const messages = [];
    for (let index = first; index <= last; index++) {
      const event = this.shown[index];
      if (event.kind === "message.created" && this.laneIndex.has(event.channel_id)) messages.push([event, this.xs[index]]);
    }
    for (const [event, x] of messages.slice(-150)) this.linkLayer.append(...this.link(event, x));
  }

  link(event, x) {
    const selected = event.id === this.selected;
    const from = this.laneY(this.laneFor(event)) + this.offsets.get(event.id), to = this.laneY(event.channel_id);
    const stroke = event.agent_id ? color(event.agent_id, this.agents) : "var(--text-3)";
    const bend = Math.sign(to - from) * Math.min(18, Math.abs(to - from) / 3);
    return [
      svg("path", { d: `M ${x} ${from} C ${x + 14} ${from + bend}, ${x + 14} ${to - bend}, ${x} ${to}`,
        class: selected ? "tl-link is-selected" : "tl-link", stroke }),
      svg("circle", { cx: x, cy: to, r: selected ? 3.5 : 2, class: "tl-link-end", fill: stroke }),
    ];
  }

  setCursor(cursor, reveal = false) {
    this.cursor = cursor;
    this.updateHeader();
    if (!this.clock) return;
    this.placePlayhead();
    const hadFocus = this.markerLayer.contains(document.activeElement);
    if (reveal) this.reveal(this.cursorX());
    this.draw();
    if (hadFocus) this.focusCursorMarker();
  }

  reveal(x) {
    const { scrollLeft, clientWidth } = this.viewport;
    if (this.width <= clientWidth) return;
    if (x >= scrollLeft + LABEL_WIDTH + 24 && x <= scrollLeft + clientWidth - 40) return;
    this.viewport.scrollLeft = Math.max(0, x - LABEL_WIDTH - (clientWidth - LABEL_WIDTH) * 0.4);
  }

  focusCursorMarker() {
    const event = this.ordered[lastAtOrBefore(this.ordered, this.cursor, (row) => row.position)];
    (this.nodes.get(event?.id) || this.viewport).focus();
  }

  setPlaying(playing) {
    this.playButton.setAttribute("aria-label", playing ? "Pause" : "Play");
    this.playButton.replaceChildren(icon(playing ? ICONS.pause : ICONS.play));
  }

  select(eventId) {
    this.selected = eventId;
    for (const node of this.nodes.values()) node.classList.toggle("is-selected", node.dataset.eventId === eventId);
    this.drawLinks();
  }

  updateHeader() {
    this.positionText.textContent = `${this.cursor} / ${this.branch?.head ?? this.events.length}`;
    const index = lastAtOrBefore(this.ordered, this.cursor, (event) => event.position);
    const current = this.ordered[index];
    this.timeText.textContent = current ? time(current.at) : "";
    let stage = null;
    for (let back = index; back >= 0 && !stage; back--) stage = stageOf(this.ordered[back]);
    this.stageText.textContent = stage || "";
  }

  stepTarget(delta) {
    const rows = this.shown.length ? this.shown : this.ordered;
    const index = lastAtOrBefore(rows, this.cursor, (event) => event.position);
    if (delta > 0) return rows[index + 1];
    return rows[rows[index]?.position === this.cursor ? index - 1 : index];
  }

  step(delta) {
    const target = this.stepTarget(delta);
    if (target) this.handlers.onSeek(target.position);
  }

  onKey(event) {
    if (event.key === "Escape" && !this.menu.hidden) {
      this.closeMenu();
      this.viewButton.focus();
      return;
    }
    if (this.menu.contains(event.target)) return;
    const onHeaderButton = event.target.closest?.(".tl-header button");
    const rows = this.shown.length ? this.shown : this.ordered;
    let position;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") position = this.stepTarget(event.key === "ArrowLeft" ? -1 : 1)?.position;
    else if (event.key === "Home") position = rows[0]?.position;
    else if (event.key === "End") position = rows.at(-1)?.position;
    else if (event.key === " " && !onHeaderButton) {
      event.preventDefault();
      this.handlers.onPlay();
      return;
    } else return;
    event.preventDefault();
    if (position !== undefined) this.handlers.onSeek(position);
  }

  // Nearest shown event to a client x, or null over the label column.
  eventAt(clientX) {
    const rect = this.viewport.getBoundingClientRect();
    if (clientX - rect.left < LABEL_WIDTH || !this.shown.length) return null;
    const x = clientX - rect.left + this.viewport.scrollLeft;
    const index = Math.max(0, lastAtOrBefore(this.xs, x, (value) => value));
    const next = Math.min(this.xs.length - 1, index + 1);
    return this.shown[Math.abs(this.xs[next] - x) < Math.abs(this.xs[index] - x) ? next : index];
  }

  seekAt(clientX) {
    const event = this.eventAt(clientX);
    if (event && event.position !== this.cursor) this.handlers.onSeek(event.position);
  }

  isEmptyLaneTarget(target) {
    return !target.closest(".tl-marker, .tl-label, .tl-ruler");
  }

  onLaneClick(event) {
    if (this.isEmptyLaneTarget(event.target)) this.seekAt(event.clientX);
  }

  onLaneContext(event) {
    if (!this.isEmptyLaneTarget(event.target)) return;
    const target = this.eventAt(event.clientX);
    if (!target) return;
    event.preventDefault();
    this.handlers.onContext(null, target.position, { x: event.clientX, y: event.clientY });
  }

  startScrub(event) {
    if (event.button !== 0 || event.target.closest(".tl-corner")) return;
    event.preventDefault();
    this.ruler.setPointerCapture?.(event.pointerId);
    this.seekAt(event.clientX);
    const move = (pointer) => this.seekAt(pointer.clientX);
    const stop = () => {
      this.ruler.removeEventListener("pointermove", move);
      this.ruler.removeEventListener("pointerup", stop);
      this.ruler.removeEventListener("pointercancel", stop);
    };
    this.ruler.addEventListener("pointermove", move);
    this.ruler.addEventListener("pointerup", stop);
    this.ruler.addEventListener("pointercancel", stop);
  }

  markerEvent(target) {
    const node = target.closest(".tl-marker");
    return node && { node, event: this.byId.get(node.dataset.eventId) };
  }

  onMarkerClick(pointer) {
    const hit = this.markerEvent(pointer.target);
    if (!hit) return;
    this.select(hit.event.id);
    this.handlers.onSelect(hit.event);
  }

  onMarkerContext(pointer) {
    const hit = this.markerEvent(pointer.target);
    if (!hit) return;
    pointer.preventDefault();
    this.handlers.onContext(hit.event, hit.event.position, { x: pointer.clientX, y: pointer.clientY });
  }

  onMarkerKey(key) {
    if (key.key !== "ContextMenu" && !(key.shiftKey && key.key === "F10")) return;
    const hit = this.markerEvent(key.target);
    if (!hit) return;
    key.preventDefault();
    key.stopPropagation();
    const rect = hit.node.getBoundingClientRect();
    this.handlers.onContext(hit.event, hit.event.position, { x: rect.left, y: rect.bottom });
  }

  onWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const offset = event.clientX - this.viewport.getBoundingClientRect().left;
    this.zoomBy(Math.exp(-event.deltaY * 0.002), Math.max(LABEL_WIDTH, offset));
  }

  zoomBy(factor, anchor) {
    if (!this.clock) return;
    const fit = this.fitScale();
    const next = clamp(this.scale * factor, fit, Math.max(fit, MAX_PX_PER_SECOND * 60));
    if (next === this.scale) return;
    const { scrollLeft, clientWidth } = this.viewport;
    const playhead = this.cursorX() - scrollLeft;
    anchor ??= playhead > LABEL_WIDTH && playhead < clientWidth ? playhead : (LABEL_WIDTH + clientWidth) / 2;
    const minutes = (scrollLeft + anchor - LABEL_WIDTH - EDGE) / this.scale;
    this.scale = next;
    this.scaleMode = "manual";
    this.layout();
    this.viewport.scrollLeft = Math.max(0, minutes * next + LABEL_WIDTH + EDGE - anchor);
    this.draw();
  }

  fit() {
    this.scaleMode = "fit";
    this.viewport.scrollLeft = 0;
    this.layout();
  }

  onMarkerHover(pointer) {
    const hit = this.markerEvent(pointer.target);
    if (!hit) return;
    const { event, node } = hit;
    const head = el("div", "tl-tip-head");
    head.append(el("strong", "", speakerName(event, this.agents)), el("span", "mono", time(event.at)));
    const parts = [head];
    if (event.stage_label) parts.push(el("div", "tl-tip-stage", event.stage_label));
    if (event.preview) parts.push(el("div", "tl-tip-preview", event.preview));
    this.tooltip.replaceChildren(...parts);
    this.tooltip.hidden = false;
    const rootRect = this.root.getBoundingClientRect(), rect = node.getBoundingClientRect();
    const width = this.tooltip.offsetWidth, height = this.tooltip.offsetHeight;
    const below = rect.bottom - rootRect.top + 6;
    const top = below + height > rootRect.height ? rect.top - rootRect.top - height - 6 : below;
    this.tooltip.style.top = `${Math.max(0, top)}px`;
    this.tooltip.style.left = `${clamp(rect.left - rootRect.left - 12, 8, rootRect.width - width - 8)}px`;
  }

  hideTooltip() {
    this.tooltip.hidden = true;
  }
}
