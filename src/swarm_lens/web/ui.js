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
export const colors = [
  "#19967d",
  "#6287bd",
  "#9276bd",
  "#ce9560",
  "#619698",
  "#a4778e",
  "#79945f",
  "#687caa",
  "#b5964f",
  "#65838e",
];
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
let toastTimer;
export function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
}
export function failure(error) {
  if (error.name === "AbortError") return;
  $("#footer-status").textContent = error.message;
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
