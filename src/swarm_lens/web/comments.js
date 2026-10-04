import { el, button, api, post, toast, failure, date, speakerName, stageOf, formatNumber } from "./ui.js";
import { field, openDialog } from "./dialog.js";

const AUTHOR_KEY = "swarm-lens:comment-author";

// No login exists; the name a reader gives is kept in this browser.
export function readAuthor() {
  try { return localStorage.getItem(AUTHOR_KEY) || ""; } catch { return ""; /* Storage unavailable; asked again next visit. */ }
}

function writeAuthor(name) {
  try { localStorage.setItem(AUTHOR_KEY, name); } catch { /* Storage unavailable; the name lasts for this visit. */ }
}

export function relativeTime(iso, now = Date.now()) {
  const minutes = Math.round((now - Date.parse(iso)) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days < 7 ? `${days}d ago` : date(iso);
}

// Threads (top-level comments with their replies) in server order: event position, then creation time.
export class CommentIndex {
  constructor(comments = []) {
    const threads = new Map();
    for (const comment of comments) if (!comment.parent_id) threads.set(comment.id, { ...comment, replies: [] });
    for (const comment of comments) if (comment.parent_id) threads.get(comment.parent_id).replies.push(comment);
    this.threads = [...threads.values()];
    this.open = this.threads.filter((thread) => !thread.resolved);
    this.byEvent = Map.groupBy(this.threads, (thread) => thread.event_id);
  }

  forEvent(eventId) {
    return this.byEvent.get(eventId) || [];
  }

  openCount(eventId) {
    return this.forEvent(eventId).filter((thread) => !thread.resolved).length;
  }
}

// The current branch's comments. Switching branches or changing a comment refetches;
// only a change made here notifies, since the caller of load() renders when it resolves.
export class CommentStore {
  constructor(onChange) {
    this.onChange = onChange;
    this.branchId = null;
    this.request = null;
    this.index = new CommentIndex();
  }

  load(branchId) {
    if (branchId === this.branchId && this.request) return this.request;
    if (branchId !== this.branchId) {
      this.branchId = branchId;
      this.index = new CommentIndex();
    }
    const request = api(`/branches/${branchId}/comments`).then(({ comments }) => {
      if (this.request !== request) return this.request;
      this.index = new CommentIndex(comments);
      return this.index;
    });
    request.catch(() => { if (this.request === request) this.request = null; });
    this.request = request;
    return request;
  }

  async reload() {
    this.request = null;
    const index = await this.load(this.branchId);
    this.onChange();
    return index;
  }

  async add({ eventId, author, text, parentId = null }) {
    await post(`/branches/${this.branchId}/comments`, { event_id: eventId, author, text, parent_id: parentId });
    return this.reload();
  }

  async update(id, changes) {
    await api(`/comments/${id}`, { method: "PATCH", body: JSON.stringify(changes) });
    return this.reload();
  }

  async remove(id) {
    await api(`/comments/${id}`, { method: "DELETE" });
    return this.reload();
  }
}

// Thread UI for the inspector. Drafts and the focused box survive inspector re-renders.
export class Comments {
  constructor({ dataChanged, viewChanged, selectEvent }) {
    this.store = new CommentStore(dataChanged);
    this.viewChanged = viewChanged;
    this.selectEvent = selectEvent;
    this.author = readAuthor();
    this.drafts = new Map();
    this.composing = null;
    this.editing = null;
    this.focusKey = null;
  }

  get index() {
    return this.store.index;
  }

  load(branchId) {
    this.composing = null;
    this.editing = null;
    return this.store.load(branchId);
  }

  setAuthor(name) {
    this.author = name;
    writeAuthor(name);
  }

  // Opens a new-thread box on the event; the caller renders the event's inspector.
  start(eventId) {
    this.composing = eventId;
    this.focusKey = `new:${eventId}`;
  }

  editAuthor() {
    const name = field("Name", "author", this.author);
    name.input.required = true;
    name.input.maxLength = 80;
    openDialog("Your name", name.fragment, { submit: () => {
      this.setAuthor(name.input.value.trim());
      this.viewChanged();
    } });
  }

  eventSection(event) {
    const threads = this.index.forEvent(event.id);
    const composing = this.composing === event.id;
    if (!threads.length && !composing) return null;
    const node = el("section", "cm-section");
    node.append(el("h3", "section-title", "Comments"), ...threads.map((thread) => this.thread(thread)));
    if (composing) node.append(this.composer(`new:${event.id}`, {
      title: "New comment", placeholder: "Comment", label: "Comment",
      submit: (text) => this.store.add({ eventId: event.id, author: this.author, text }),
      close: () => (this.composing = null),
    }));
    return node;
  }

  overviewSection(events, agents) {
    const open = this.index.open;
    if (!open.length) return null;
    const byId = new Map(events.map((event) => [event.id, event]));
    const node = el("section", "cm-section");
    node.append(el("h3", "section-title", `Comments (${formatNumber(open.length)} open)`));
    for (const thread of open) node.append(this.summary(thread, byId.get(thread.event_id), agents));
    return node;
  }

  summary(thread, event, agents) {
    const row = button("", () => this.selectEvent(thread.event_id), "cm-summary");
    const head = el("span", "cm-head");
    head.append(el("span", "cm-author", thread.author), this.when(thread.created_at));
    if (thread.replies.length) head.append(el("span", "cm-replies muted", `${thread.replies.length} repl${thread.replies.length === 1 ? "y" : "ies"}`));
    const anchor = event ? [speakerName(event, agents), stageOf(event) || `#${event.position}`].join(" · ") : `#${thread.position}`;
    row.append(head, el("span", "cm-summary-text", thread.text), el("span", "cm-anchor muted", anchor));
    return row;
  }

  thread(thread) {
    const node = el("article", `cm-thread${thread.resolved ? " is-resolved" : ""}`);
    node.append(this.comment(thread, thread), ...thread.replies.map((reply) => this.comment(reply, thread)));
    if (!thread.resolved) node.append(this.composer(`reply:${thread.id}`, {
      title: "Reply", placeholder: "Reply", label: "Reply",
      submit: (text) => this.store.add({ eventId: thread.event_id, author: this.author, text, parentId: thread.id }),
    }));
    return node;
  }

  comment(comment, thread) {
    const node = el("div", "cm-comment");
    const head = el("div", "cm-head");
    head.append(el("span", "cm-author", comment.author), this.when(comment.created_at));
    const tools = el("span", "cm-tools");
    if (comment === thread) tools.append(this.tool(thread.resolved ? "Reopen" : "Resolve",
      () => this.store.update(thread.id, { resolved: !thread.resolved })));
    if (comment.author === this.author) tools.append(
      this.tool("Edit", () => this.edit(comment.id)),
      this.tool("Delete", () => this.confirmDelete(comment, comment === thread ? thread.replies.length : 0)),
    );
    head.append(tools);
    node.append(head, this.editing === comment.id ? this.composer(`edit:${comment.id}`, {
      title: "Edit comment", initial: comment.text, label: "Save",
      submit: (text) => this.store.update(comment.id, { text }),
      close: () => (this.editing = null),
    }) : el("p", "cm-text", comment.text));
    return node;
  }

  when(iso) {
    const node = el("time", "cm-time muted", relativeTime(iso));
    node.dateTime = iso;
    node.title = new Date(iso).toLocaleString();
    return node;
  }

  tool(label, action) {
    return button(label, () => Promise.resolve(action()).catch(failure), "ghost cm-tool");
  }

  edit(id) {
    this.editing = id;
    this.focusKey = `edit:${id}`;
    this.viewChanged();
  }

  confirmDelete(comment, replies) {
    const detail = replies ? `Its ${replies} repl${replies === 1 ? "y is" : "ies are"} deleted too.` : comment.text;
    openDialog("Delete comment?", el("p", "cm-confirm", detail), { confirm: "Delete", pending: "Deleting…",
      submit: () => this.store.remove(comment.id) });
  }

  // A box for a new thread, a reply or an edit. ⌘/Ctrl+Enter submits; Escape cancels.
  composer(key, { title, placeholder = "", label, initial = "", submit, close }) {
    const form = el("form", "cm-composer");
    const name = this.author ? null : this.nameInput();
    const text = el("textarea", "cm-input");
    text.value = this.drafts.get(key) ?? initial;
    text.placeholder = placeholder;
    text.required = true;
    text.maxLength = 10000;
    text.dataset.draft = key;
    text.setAttribute("aria-label", title);
    form.classList.toggle("is-open", Boolean(close || text.value));
    const cancel = () => {
      this.drafts.delete(key);
      this.focusKey = null;
      close?.();
      this.viewChanged();
    };
    text.addEventListener("input", () => {
      this.drafts.set(key, text.value);
      form.classList.toggle("is-open", Boolean(close || text.value));
    });
    text.addEventListener("focus", () => (this.focusKey = key));
    // A re-render detaches the box while it has focus; only a real move elsewhere forgets it.
    text.addEventListener("blur", () => setTimeout(() => {
      if (text.isConnected && this.focusKey === key) this.focusKey = null;
    }));
    text.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        form.requestSubmit();
      } else if (event.key === "Escape") {
        event.stopPropagation();
        cancel();
      }
    });
    const actions = el("div", "cm-actions");
    const send = el("button", "primary", label);
    send.type = "submit";
    actions.append(button("Cancel", cancel, "ghost"), send);
    form.append(...(name ? [name] : []), text, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      send.disabled = true;
      try {
        if (name) this.setAuthor(name.value.trim());
        await submit(text.value.trim());
        this.drafts.delete(key);
        this.focusKey = null;
        close?.();
        this.viewChanged();
      } catch (error) {
        send.disabled = false;
        failure(error);
      }
    });
    return form;
  }

  nameInput() {
    const input = el("input", "cm-name");
    input.placeholder = "Your name";
    input.required = true;
    input.maxLength = 80;
    input.setAttribute("aria-label", "Your name");
    return input;
  }

  // Called after the inspector renders: puts the caret back in the box the reader was typing in.
  restoreFocus(root) {
    if (!this.focusKey) return;
    const input = [...root.querySelectorAll("[data-draft]")].find((node) => node.dataset.draft === this.focusKey);
    if (!input) return void (this.focusKey = null);
    input.focus({ preventScroll: true });
    input.setSelectionRange(input.value.length, input.value.length);
    input.scrollIntoView({ block: "nearest" });
  }
}

// Run sharing

function downloadName(disposition, fallback) {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "")?.[1];
  return encoded ? decodeURIComponent(encoded) : fallback;
}

export async function exportRun(run) {
  const response = await fetch(`/api/runs/${run.id}/export`);
  if (!response.ok) throw new Error((await response.json()).detail);
  const url = URL.createObjectURL(await response.blob());
  const link = el("a");
  link.href = url;
  link.download = downloadName(response.headers.get("Content-Disposition"), `${run.name}.swarm-lens.json`);
  link.click();
  URL.revokeObjectURL(url);
}

// Resolves with the imported run's root branch, or null when the reader closes the picker.
export function importRun() {
  return new Promise((resolve, reject) => {
    const input = el("input");
    input.type = "file";
    input.accept = ".json,application/json";
    input.addEventListener("cancel", () => resolve(null));
    input.addEventListener("change", async () => {
      try {
        const file = input.files[0];
        if (!file) return resolve(null);
        toast("Importing run…");
        const result = await api("/runs/import", { method: "POST", body: await file.text() });
        resolve(result.branch);
      } catch (error) { reject(error); }
    });
    input.click();
  });
}
