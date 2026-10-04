import { el, button, eventTone } from "./ui.js";
import { PlaybackBar } from "./timeline.js";

const VIEW_KEY = "swarm-lens:visualization";
const RATE_KEY = "swarm-lens:playback-rate";
export const PLAYBACK_RATES = [1, 2, 5, 10];
const DEFAULT_RATE = 2;
// Views without their own notion of a step move between the events Lanes shows by default.
const STEP_TONES = new Set(["message", "tool", "intervention"]);

const registry = new Map();
const hosts = new Set();

// Registers a view for the shared cursor area. `mount(root, actions, toolbar)` returns
// `{ update(context), destroy(), stepTarget?(delta) }`; only the visible view is mounted and updated.
export function registerVisualization(config) {
  const { id, title, mount } = config || {};
  if (!id || !title || typeof mount !== "function") throw new Error("A visualization needs an id, a title and a mount function.");
  if (registry.has(id)) throw new Error(`Visualization already registered: ${id}`);
  registry.set(id, config);
  for (const host of hosts) host.add(config);
}

function readStored(key) {
  try { return localStorage.getItem(key); } catch { return null; /* Storage unavailable; defaults apply. */ }
}

function writeStored(key, value) {
  try { localStorage.setItem(key, String(value)); } catch { /* Storage unavailable; the choice lasts for this visit. */ }
}

export function playbackRate() {
  const saved = Number(readStored(RATE_KEY));
  return PLAYBACK_RATES.includes(saved) ? saved : DEFAULT_RATE;
}

export const playbackInterval = () => 1000 / playbackRate();

export function defaultStepTarget(events, cursor, delta) {
  const steps = events.filter((event) => STEP_TONES.has(eventTone(event)));
  return (delta > 0 ? steps.find((event) => event.position > cursor) : steps.findLast((event) => event.position < cursor))?.position;
}

function mountedInstance(config, root, actions, toolbar) {
  const instance = config.mount(root, actions, toolbar);
  if (typeof instance?.update !== "function" || typeof instance.destroy !== "function")
    throw new Error(`Visualization ${config.id} must return { update, destroy } from mount.`);
  return instance;
}

export class VisualizationHost {
  constructor(root, actions) {
    this.root = root;
    this.actions = actions;
    this.views = new Map();
    this.active = null;
    this.context = null;
    this.preferred = readStored(VIEW_KEY);
    this.build();
    hosts.add(this);
    for (const config of registry.values()) this.addTab(config);
    const initial = this.views.has(this.preferred) ? this.preferred : this.views.keys().next().value;
    if (initial) this.show(initial);
  }

  build() {
    this.switcher = el("div", "segmented viz-switch");
    this.switcher.setAttribute("role", "group");
    this.switcher.setAttribute("aria-label", "Visualization");
    this.playback = new PlaybackBar({ onStep: (delta) => this.step(delta), onPlay: () => this.actions.togglePlayback() });
    this.tools = el("div", "viz-tools");
    const header = el("header", "viz-header");
    header.append(this.switcher, ...this.playback.nodes, this.speedControl(), el("span", "viz-spacer"), this.tools);
    this.body = el("div", "viz-body");
    this.root.classList.add("viz");
    this.root.replaceChildren(header, this.body);
    this.root.addEventListener("keydown", (event) => this.onKey(event));
  }

  speedControl() {
    const select = el("select", "viz-speed");
    select.setAttribute("aria-label", "Playback speed");
    select.title = "Playback speed";
    for (const rate of PLAYBACK_RATES) {
      const option = el("option", "", `${rate}/s`);
      option.value = rate;
      select.append(option);
    }
    select.value = playbackRate();
    select.addEventListener("change", () => writeStored(RATE_KEY, select.value));
    return select;
  }

  // A view registered later (a plugin) opens at once only if it was the remembered choice.
  add(config) {
    this.addTab(config);
    if (!this.active || config.id === this.preferred) this.show(config.id);
  }

  addTab(config) {
    const tab = button(config.title, () => this.show(config.id, { remember: true }));
    tab.setAttribute("aria-pressed", "false");
    this.switcher.append(tab);
    const root = el("div", "viz-view");
    root.hidden = true;
    this.body.append(root);
    this.views.set(config.id, { config, tab, root, tools: el("div", "viz-view-tools"), instance: null });
  }

  show(id, { remember = false } = {}) {
    const next = this.views.get(id);
    if (!next) throw new Error(`Visualization is unavailable: ${id}`);
    if (remember) writeStored(VIEW_KEY, id);
    if (this.active === id) return;
    this.unmount(this.views.get(this.active));
    this.active = id;
    for (const view of this.views.values()) view.tab.setAttribute("aria-pressed", String(view === next));
    next.root.hidden = false;
    this.tools.replaceChildren(next.tools);
    next.instance = mountedInstance(next.config, next.root, this.actions, next.tools);
    if (this.context) next.instance.update(this.context);
  }

  // A hidden view keeps no DOM, listeners or timers.
  unmount(view) {
    if (!view?.instance) return;
    view.instance.destroy();
    view.instance = null;
    view.root.replaceChildren();
    view.root.className = "viz-view";
    view.root.hidden = true;
    view.tools.replaceChildren();
  }

  update(context) {
    this.context = context;
    this.playback.update(context.events, context.branch, context.cursor);
    this.views.get(this.active)?.instance.update(context);
  }

  setPlaying(playing) {
    this.playback.setPlaying(playing);
  }

  step(delta) {
    if (!this.context) return;
    const instance = this.views.get(this.active)?.instance;
    const target = instance?.stepTarget ? instance.stepTarget(delta) : defaultStepTarget(this.context.events, this.context.cursor, delta);
    if (target !== undefined) this.actions.seek(target);
  }

  // Views handle their own keys first; anything left over moves the shared cursor.
  onKey(event) {
    if (event.defaultPrevented || event.target.closest("select, input, textarea")) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      this.step(event.key === "ArrowLeft" ? -1 : 1);
    } else if (event.key === " " && !event.target.closest("button")) {
      event.preventDefault();
      this.actions.togglePlayback();
    }
  }
}
