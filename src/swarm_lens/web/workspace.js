import { $, el, button, failure } from "./ui.js";

// Views keep their DOM and timeline state while another renderer occupies the workspace.
export class WorkspaceViews {
  constructor(onSelect) {
    this.entries = new Map();
    this.current = null;
    this.transition = 0;
    this.onSelect = onSelect;
    $("#workspace-tabs").onkeydown = (event) => {
      const tabs = [...this.entries.values()].map((entry) => entry.tab);
      const index = tabs.indexOf(document.activeElement);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].focus();
      tabs[next].click();
    };
  }

  register({ id, title, panel, onShow, onHide }) {
    if (this.entries.has(id)) throw new Error(`Workspace view already registered: ${id}`);
    panel ||= el("section", "plugin-workspace");
    panel.id = `workspace-${id}`;
    panel.hidden = true;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `view-${id}`);
    if (!panel.isConnected) $("#workspace-views").append(panel);
    const tab = button(title, () => this.onSelect(id));
    tab.id = `view-${id}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panel.id);
    tab.setAttribute("aria-selected", "false");
    tab.tabIndex = -1;
    $("#workspace-tabs").append(tab);
    this.entries.set(id, { id, panel, tab, onShow, onHide, params: {}, scroll: 0 });
    return panel;
  }

  show(id, params = {}) {
    const transition = ++this.transition;
    const next = this.entries.get(id);
    if (!next) throw new Error(`Workspace view is unavailable: ${id}`);
    const previous = this.entries.get(this.current);
    if (previous) {
      previous.scroll = $("#workspace-views").scrollTop;
      previous.scrollNodes = [...previous.panel.querySelectorAll("[data-view-scroll]")]
        .map((node) => ({ node, left: node.scrollLeft, top: node.scrollTop }));
      previous.onHide?.();
    }
    this.current = id;
    next.params = params;
    for (const entry of this.entries.values()) {
      entry.panel.hidden = entry !== next;
      entry.tab.classList.toggle("active", entry === next);
      entry.tab.setAttribute("aria-selected", String(entry === next));
      entry.tab.tabIndex = entry === next ? 0 : -1;
    }
    Promise.resolve(next.onShow?.(params)).catch(failure);
    requestAnimationFrame(() => {
      if (this.current !== id || this.transition !== transition) return;
      $("#workspace-views").scrollTop = next.scroll;
      for (const { node, left, top } of next.scrollNodes || []) node.scrollTo(left, top);
    });
  }

  reset() {
    this.entries.get(this.current)?.onHide?.();
    this.current = null;
    this.transition++;
    for (const entry of this.entries.values()) {
      entry.params = {};
      entry.scroll = 0;
      entry.scrollNodes = [];
    }
  }

  get params() { return this.entries.get(this.current)?.params || {}; }
}

const RESERVED = new Set(["branch", "cursor", "view"]);

// View-specific params (a report id, compared branches) ride along in the hash.
export function workspaceURL({ branchId, cursor, view = "timeline", ...params }) {
  const url = new URL(location.href);
  const hash = new URLSearchParams({ branch: branchId, cursor: String(cursor), view });
  for (const [key, value] of Object.entries(params)) if (value && !RESERVED.has(key)) hash.set(key, value);
  url.hash = hash.toString();
  return url.href;
}

export function readWorkspaceRoute() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const cursor = hash.get("cursor");
  const params = Object.fromEntries([...hash].filter(([key]) => !RESERVED.has(key)));
  return { branchId: hash.get("branch"), view: hash.get("view") || "timeline", params,
    cursor: cursor !== null && /^\d+$/.test(cursor) && Number.isSafeInteger(Number(cursor)) ? Number(cursor) : undefined };
}
