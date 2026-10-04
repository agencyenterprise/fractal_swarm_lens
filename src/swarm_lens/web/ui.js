export const $ = (selector) => document.querySelector(selector);
export const $$ = (selector) => [...document.querySelectorAll(selector)];
export const formatNumber = (value) =>
  new Intl.NumberFormat("en").format(value);
export const time = (value) =>
  value ? new Date(value).toISOString().slice(11, 19) : "—";
export const date = (value) =>
  value
    ? new Date(value).toLocaleDateString("en", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      })
    : "—";
// Agent hues, readable on light and dark surfaces.
export const colors = ["#2f9e83", "#5b84c4", "#9a74c9", "#d0874f", "#4f9aa3", "#c26d8f", "#7f9a4f", "#6f7fd1", "#b8913b", "#6b8794"];
export function color(id, agents) {
  return colors[
    Math.max(0, Object.keys(agents || {}).indexOf(id)) % colors.length
  ];
}
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
export function button(label, action, className = "") {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
export function section(title, text) {
  const node = el("section", "inspector-section");
  node.append(el("h3", "", title));
  if (text !== undefined) node.append(el("p", "", text));
  return node;
}
export async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
export const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
// Tooltips: any element with `data-tip` shows one shared tooltip after a short hover or on keyboard focus.
export const TIP_DELAY = 400;
const TIP_GAP = 6, TIP_MARGIN = 8;
const tipState = { node: null, target: null, timer: null };

export function tip(node, text) {
  if (text) node.dataset.tip = text;
  else delete node.dataset.tip;
  return node;
}

function tipNode() {
  if (!tipState.node?.isConnected) {
    tipState.node = el("div", "tooltip");
    tipState.node.id = "tooltip";
    tipState.node.setAttribute("role", "tooltip");
    tipState.node.hidden = true;
    document.body.append(tipState.node);
  }
  return tipState.node;
}

function describedBy(target, add) {
  const ids = (target.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== "tooltip");
  if (add) ids.push("tooltip");
  if (ids.length) target.setAttribute("aria-describedby", ids.join(" "));
  else target.removeAttribute("aria-describedby");
}

function placeTip(node, target) {
  const anchor = target.getBoundingClientRect();
  const width = node.offsetWidth, height = node.offsetHeight;
  const below = anchor.bottom + TIP_GAP;
  const top = below + height > innerHeight - TIP_MARGIN ? anchor.top - TIP_GAP - height : below;
  const left = anchor.left + anchor.width / 2 - width / 2;
  node.style.left = `${Math.max(TIP_MARGIN, Math.min(left, innerWidth - width - TIP_MARGIN))}px`;
  node.style.top = `${Math.max(TIP_MARGIN, top)}px`;
}

function showTip(target) {
  clearTimeout(tipState.timer);
  const text = target.dataset.tip;
  if (!text || !target.isConnected) return hideTip();
  if (tipState.target && tipState.target !== target) describedBy(tipState.target, false);
  const node = tipNode();
  node.textContent = text;
  node.hidden = false;
  placeTip(node, target);
  tipState.target = target;
  describedBy(target, true);
}

export function hideTip() {
  clearTimeout(tipState.timer);
  if (tipState.target) describedBy(tipState.target, false);
  tipState.target = null;
  if (tipState.node) tipState.node.hidden = true;
}

const tipTarget = (event) => event.target.closest?.("[data-tip]");
// Text fields keep focus while typing; a tooltip there would cover what is typed.
const isTextField = (node) => node.matches("input:not([type=checkbox]):not([type=radio]), textarea, select");

export function installTooltips(root = document) {
  root.addEventListener("pointerover", (event) => {
    const target = tipTarget(event);
    if (!target || target === tipState.target) return;
    hideTip();
    tipState.timer = setTimeout(() => showTip(target), TIP_DELAY);
  });
  root.addEventListener("pointerout", (event) => {
    const target = tipTarget(event);
    if (target && !target.contains(event.relatedTarget)) hideTip();
  });
  root.addEventListener("focusin", (event) => {
    const target = tipTarget(event);
    if (target && target === event.target && !isTextField(target) && target.matches(":focus-visible")) showTip(target);
  });
  root.addEventListener("focusout", (event) => {
    if (tipTarget(event) === tipState.target) hideTip();
  });
  root.addEventListener("keydown", (event) => { if (event.key === "Escape") hideTip(); }, true);
  root.addEventListener("pointerdown", hideTip, true);
  root.addEventListener("scroll", hideTip, true);
}

let toastTimer;
export function toast(message, duration = 4500) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), duration);
}
export function failure(error) {
  if (error.name === "AbortError") return;
  console.error(error);
  toast(error.message);
}
export function svg(tag, attrs = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([key, value]) =>
    node.setAttribute(key, value),
  );
  if (text !== undefined) node.textContent = text;
  return node;
}

export function logoFor(agent) {
  const name = `${agent?.name || ""} ${agent?.model || ""}`.toLowerCase();
  if (name.includes("claude") || name.includes("anthropic"))
    return "/assets/logos/claude-color.svg";
  if (name.includes("gemini")) return "/assets/logos/gemini-color.svg";
  if (name.includes("deepseek")) return "/assets/logos/deepseek-color.svg";
  if (name.includes("gpt") || name.includes("openai"))
    return "/assets/logos/openai.svg";
  return null;
}

// One visual category per event; every module colors and filters by it.
export function eventTone(event) {
  if (event.intervention) return "intervention";
  const family = event.kind.split(".")[0];
  if (["message", "tool", "memory"].includes(family)) return family;
  if (family === "observation") return "observation";
  return "state";
}

// Stage labels look like "Debate round 6 · model input"; the part before " · " groups events.
export function stageOf(event) {
  return event.stage_label ? event.stage_label.split(" · ")[0] : null;
}

export function speakerName(event, agents) {
  return agents?.[event.agent_id]?.name || (event.kind === "message.created" ? "Human" : "System");
}

export function avatar(agent, className = "avatar") {
  const node = el("span", className);
  const logo = logoFor(agent);
  if (logo) {
    const image = el("img");
    image.src = logo;
    image.alt = "";
    node.append(image);
  } else node.textContent = (agent?.name || "?").slice(0, 1).toUpperCase();
  return node;
}
