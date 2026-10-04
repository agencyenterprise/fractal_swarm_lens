import { el, button, api, failure } from "./ui.js";
import { renderMastMessage } from "./mast-message.js";

// Each row owns its disclosure and fetch lifecycle; opening it never runs a judge.
export function mastTraitDetails(label, job, host) {
  const root = el("details", "mast-trait");
  const summary = el("summary", "mast-label-row");
  const name = el("div");
  const arrow = el("span", "mast-trait-arrow", "›");
  arrow.setAttribute("aria-hidden", "true");
  name.append(arrow, el("span", "mast-code", label.code), el("span", "", label.label));
  summary.append(name, el("span", `mast-badge ${label.present === true ? "present" : label.present === null ? "unknown" : ""}`,
    label.present === null ? "Unparsed" : label.present ? "Present" : "Absent"));
  const body = el("div", "mast-trait-body");
  body.setAttribute("aria-live", "polite");
  root.append(summary, body);
  let loaded = false, loading = false;
  async function load() {
    if (loaded || loading) return;
    loading = true;
    body.replaceChildren(el("p", "muted", "Loading trait details…"));
    try {
      const detail = await api(`/plugins/mast/analyses/${encodeURIComponent(job.id)}/traits/${encodeURIComponent(label.code)}`);
      body.replaceChildren();
      if (detail.explanation) body.append(el("p", "mast-trait-text", detail.explanation));
      if (detail.notice) body.append(el("p", "muted", detail.notice));
      for (const [index, occurrence] of detail.occurrences.entries()) {
        const section = el("section", "mast-occurrence");
        section.append(el("h5", "", `Occurrence ${index + 1} · events ${occurrence.start_position}–${occurrence.end_position}`),
          el("p", "mast-trait-text", occurrence.explanation),
          button("View span end on timeline →", () => host.showSnapshot(job.branch_id, occurrence.end_position).catch(failure), "subtle"));
        const supportingCount = occurrence.events.filter(event => event.role === "supporting").length;
        const counterCount = occurrence.events.filter(event => event.role === "counterevidence").length;
        const contextCount = occurrence.events.filter(event => event.role === "context").length;
        const controls = el("div", "mast-evidence-controls");
        controls.append(el("span", "muted", `${supportingCount} supporting · ${counterCount} counterevidence · ${contextCount} context`));
        const contextToggle = button(`Show full context (${contextCount})`, () => {
          const expanded = contextToggle.getAttribute("aria-pressed") !== "true";
          contextToggle.setAttribute("aria-pressed", String(expanded));
          contextToggle.textContent = expanded ? "Show evidence only" : `Show full context (${contextCount})`;
          for (const item of events.querySelectorAll(".mast-evidence-event.context")) item.hidden = !expanded;
        }, "subtle");
        contextToggle.setAttribute("aria-pressed", "false");
        contextToggle.hidden = !contextCount;
        controls.append(contextToggle);
        section.append(controls);
        const events = el("ol", "mast-evidence-timeline");
        let openedFirst = false;
        for (const event of occurrence.events) {
          const item = el("li", `mast-evidence-event ${event.role}`);
          item.hidden = event.role === "context";
          const message = el("details", "mast-evidence-message");
          const heading = el("summary");
          const identity = el("span", "mast-evidence-identity");
          identity.append(el("strong", "", event.sender || event.kind || "Recorded event"),
            el("span", "muted", `Event ${event.position}`));
          heading.append(identity,
            el("span", "mast-evidence-role", { supporting: "Supporting evidence", counterevidence: "Counterevidence", context: "Context" }[event.role]));
          const preview = el("span", "mast-evidence-preview", event.text.replace(/\s+/g, " ").slice(0, 180));
          heading.append(preview);
          const content = el("div", "mast-message-content");
          content.tabIndex = 0;
          content.setAttribute("role", "region");
          content.setAttribute("aria-label", `Message at event ${event.position}`);
          const tools = el("div", "mast-message-tools");
          const at = new Date(event.at);
          const timestamp = el("time", "muted", Number.isNaN(at.getTime()) ? event.at : `${at.toISOString().slice(0, 10)} · ${at.toISOString().slice(11, 19)} UTC`);
          timestamp.title = event.at;
          const original = button("Original text", () => {
            const showOriginal = original.getAttribute("aria-pressed") !== "true";
            original.setAttribute("aria-pressed", String(showOriginal));
            original.textContent = showOriginal ? "Formatted text" : "Original text";
            if (showOriginal) content.replaceChildren(el("pre", "mast-message-original", event.text));
            else renderContent();
          }, "subtle");
          original.setAttribute("aria-pressed", "false");
          tools.append(timestamp, original,
            button("View event on timeline →", () => host.showEvidenceEvent(job.branch_id, event.position, event.event_id).catch(failure), "subtle mast-event-link"));
          let rendered = false;
          function renderContent() {
            // JSON tool/state payloads stay structured; conversation text gets Markdown + math.
            try {
              const value = JSON.parse(event.text);
              if (value && typeof value === "object") {
                content.replaceChildren(el("pre", "mast-message-original", JSON.stringify(value, null, 2)));
                return;
              }
            } catch { /* Ordinary message text. */ }
            content.innerHTML = renderMastMessage(event.text);
          }
          message.addEventListener("toggle", () => {
            if (message.open && !rendered) { renderContent(); rendered = true; }
          });
          message.append(heading, tools, content);
          // Start with one readable message, not dozens of fully expanded responses.
          if (!openedFirst && event.role !== "context") {
            message.open = true;
            renderContent();
            rendered = openedFirst = true;
          }
          item.append(message);
          events.append(item);
        }
        section.append(events);
        body.append(section);
      }
      if (detail.definition) {
        const definition = el("details", "mast-details");
        definition.append(el("summary", "", "Trait definition"), el("p", "mast-trait-text", detail.definition));
        body.append(definition);
      }
      if (detail.summary) {
        const context = el("details", "mast-details");
        context.append(el("summary", "", "Overall assessment summary"), el("p", "mast-trait-text", detail.summary));
        body.append(context);
      }
      body.append(button(`View analyzed snapshot · events 1–${job.cursor} →`,
        () => host.showSnapshot(job.branch_id, job.cursor).catch(failure), "subtle"));
      loaded = true;
    } catch (error) {
      body.replaceChildren(el("p", "mast-error", error.message), button("Retry", load));
    } finally {
      loading = false;
    }
  }
  root.addEventListener("toggle", () => { if (root.open) load(); });
  return root;
}
