import { $, $$, el, button, svg, time, color, logoFor } from "./ui.js";

const LEFT = 166,
  ROW = 44,
  RULER = 32;

export class EventTimeline {
  constructor({ onSeek, onAgent, onEvent }) {
    this.onSeek = onSeek;
    this.onAgent = onAgent;
    this.onEvent = onEvent;
    this.events = [];
    this.agents = {};
    this.lanes = [];
    this.cursor = 0;
    this.scale = 35;
    this.selected = null;
    this.viewport = $("#timeline-viewport");
    this.scene = $("#timeline-scene");
    this.viewport.addEventListener("scroll", () => {
      if (!this.frame)
        this.frame = requestAnimationFrame(() => {
          this.frame = null;
          this.drawEvents();
        });
    });
    this.viewport.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      this.seekAt(event.clientX);
    });
    $("#zoom-in").onclick = () => this.zoom(this.scale * 1.8);
    $("#zoom-out").onclick = () => this.zoom(this.scale / 1.8);
    $("#timeline-fit").onclick = () =>
      this.zoom(
        Math.max(1, this.viewport.clientWidth - LEFT - 40) /
          ((this.end - this.start) / 60000),
      );
    $$("[data-timeline-kind]").forEach(
      (input) => (input.onchange = () => this.drawEvents()),
    );
    $("#timeline-connections").onchange = () => this.drawEvents();
    $("#timeline-expand").onclick = () =>
      this.setHeight(
        this.panelHeight() > 570
          ? 400
          : Math.max(620, Math.min(850, window.innerHeight - 130)),
      );
    const resize = $("#resize-timeline");
    let resizeOrigin;
    resize.onpointerdown = (event) => {
      resizeOrigin = { y: event.clientY, height: this.panelHeight() };
      resize.setPointerCapture(event.pointerId);
    };
    resize.onpointermove = (event) => {
      if (resize.hasPointerCapture(event.pointerId))
        this.setHeight(resizeOrigin.height + event.clientY - resizeOrigin.y);
    };
    resize.onkeydown = (event) => {
      if (["ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        this.setHeight(
          this.panelHeight() + (event.key === "ArrowDown" ? 40 : -40),
        );
      }
    };
    const saved = Number(localStorage.getItem("swarm-lens.timeline.height"));
    if (saved >= 260 && saved <= 1200) this.setHeight(saved);
    const handle = $("#playhead-handle");
    handle.onpointerdown = (event) => {
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = (event) => {
      if (handle.hasPointerCapture(event.pointerId)) this.seekAt(event.clientX);
    };
    handle.onkeydown = (event) => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        this.onSeek(this.cursor + (event.key === "ArrowLeft" ? -1 : 1));
      }
    };
    new ResizeObserver(() => {
      if (this.events.length) this.layout();
    }).observe(this.viewport);
  }

  panelHeight() {
    return $(".event-timeline").getBoundingClientRect().height;
  }
  setHeight(height) {
    height = Math.round(Math.max(260, Math.min(1200, height)));
    document.documentElement.style.setProperty(
      "--timeline-height",
      height + "px",
    );
    localStorage.setItem("swarm-lens.timeline.height", height);
    $("#timeline-expand").textContent =
      height > 570 ? "Compact ↕" : "Expand ↕";
  }
  x(at) {
    return (
      LEFT +
      (((typeof at === "number" ? at : Date.parse(at)) - this.start) / 60000) *
        this.scale +
      20
    );
  }
  laneFor(event) {
    if (event.agent_id) return event.agent_id;
    return event.kind === "message.created" ? "__human" : "__environment";
  }

  setData(events, branch) {
    $("#timeline-events").replaceChildren();
    this.events = events;
    this.branch = branch;
    this.selected = null;
    this.agents = {};
    const channels = new Map();
    for (const event of events) {
      if (event.kind === "agent.added" || event.kind === "agent.updated")
        this.agents[event.agent_id] = {
          id: event.agent_id,
          name: event.agent_name || event.preview,
          model: event.model,
        };
      if (event.kind === "channel.created")
        channels.set(event.entity_id, {
          id: event.entity_id,
          name: event.channel_name || event.preview,
        });
    }
    this.lanes = [...channels.values()].map((channel) => ({
      ...channel,
      kind: "channel",
    }));
    this.lanes.push(
      ...Object.values(this.agents).map((agent) => ({
        ...agent,
        kind: "agent",
      })),
    );
    if (
      events.some(
        (event) => event.kind === "message.created" && !event.agent_id,
      )
    )
      this.lanes.push({ id: "__human", name: "Human", kind: "human" });
    this.lanes.push({
      id: "__environment",
      name: "Environment",
      kind: "environment",
    });
    this.laneIndex = new Map(this.lanes.map((lane, index) => [lane.id, index]));
    const actual = events.filter(
      (event) =>
        !["agent.added", "channel.created", "environment.updated"].includes(
          event.kind,
        ) || event.intervention,
    );
    this.ordered = [...events].sort(
      (a, b) => Date.parse(a.at) - Date.parse(b.at) || a.position - b.position,
    );
    this.start = Math.min(
      ...(actual.length ? actual : events).map((event) => Date.parse(event.at)),
    );
    this.end = Math.max(
      this.start + 60000,
      ...events.map((event) => Date.parse(event.at)),
    );
    if (!Number.isFinite(this.start)) {
      this.start = Date.now();
      this.end = this.start + 60000;
    }
    $("#timeline-subtitle").textContent =
      `${Object.keys(this.agents).length} agents · ${channels.size} channel${channels.size === 1 ? "" : "s"} · timestamped events`;
    this.layout();
  }

  zoom(scale) {
    this.scale = Math.max(0.5, Math.min(1500, scale));
    this.layout();
    this.setCursor(this.cursor, true);
  }

  layout() {
    const width = Math.max(
      this.viewport.clientWidth,
      LEFT + ((this.end - this.start) / 60000) * this.scale + 55,
    );
    this.scene.style.width = width + "px";
    this.scene.style.height = RULER + this.lanes.length * ROW + "px";
    const ruler = $("#timeline-ruler");
    ruler.replaceChildren();
    const axis = el("div", "timeline-axis-label", "AGENT / UTC");
    ruler.append(axis);
    const minutesPerTick =
      [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120].find(
        (value) => value * this.scale >= 95,
      ) || 240;
    const step = minutesPerTick * 60000;
    for (
      let at = Math.ceil(this.start / step) * step;
      at <= this.end;
      at += step
    ) {
      const tick = el(
        "span",
        "time-tick",
        time(at).slice(0, minutesPerTick < 1 ? 8 : 5),
      );
      tick.style.left = this.x(at) + "px";
      ruler.append(tick);
    }
    const lanes = $("#timeline-lanes");
    lanes.replaceChildren();
    for (const lane of this.lanes) {
      const row = el("div", "time-lane " + lane.kind),
        label = button(
          "",
          () => lane.kind === "agent" && this.onAgent(lane.id),
          "lane-label",
        );
      label.setAttribute(
        "aria-label",
        lane.kind === "agent"
          ? `Inspect timeline agent ${lane.name}`
          : lane.name + " lane",
      );
      const logo = logoFor(lane),
        avatar = el("span", "lane-avatar");
      if (logo) {
        const image = el("img");
        image.src = logo;
        image.alt = "";
        avatar.append(image);
      } else
        avatar.textContent =
          lane.kind === "channel" ? "#" : lane.kind === "human" ? "H" : "◇";
      if (lane.kind === "agent")
        avatar.style.borderColor = color(lane.id, this.agents) + "55";
      const text = el("span", "lane-name", lane.name);
      label.append(avatar, text);
      label.title = lane.name;
      row.append(label, el("div", "lane-line"));
      lanes.append(row);
    }
    const connections = $("#timeline-connections-svg");
    connections.setAttribute("width", width);
    connections.setAttribute("height", RULER + this.lanes.length * ROW);
    const windowMinutes = Math.max(
      0.01,
      (this.viewport.clientWidth - LEFT) / this.scale,
    );
    $("#zoom-label").textContent =
      windowMinutes < 1
        ? Math.round(windowMinutes * 60) + " sec"
        : windowMinutes >= 60
          ? (windowMinutes / 60).toFixed(1) + " hr"
          : Math.round(windowMinutes) + " min";
    this.drawEvents();
    this.setCursor(this.cursor, false);
  }

  allowed(event) {
    const family = event.intervention
      ? "intervention"
      : event.kind.split(".")[0];
    return !!document.querySelector(`[data-timeline-kind="${family}"]`)
      ?.checked;
  }

  drawEvents() {
    const root = $("#timeline-events");
    const existing = new Map(
      [...root.querySelectorAll("button")].map((node) => [
        node.dataset.eventId,
        node,
      ]),
    );
    root.querySelector(".fork-marker")?.remove();
    const connections = $("#timeline-connections-svg");
    connections.replaceChildren();
    const defs = svg("defs"),
      marker = svg("marker", {
        id: "broadcast-arrow",
        viewBox: "0 0 8 8",
        refX: 7,
        refY: 4,
        markerWidth: 5,
        markerHeight: 5,
        orient: "auto",
      });
    marker.append(svg("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#5b9d8b" }));
    defs.append(marker);
    connections.append(defs);
    const start = this.viewport.scrollLeft + LEFT - 30,
      end = this.viewport.scrollLeft + this.viewport.clientWidth + 80;
    const visible = this.events.filter(
      (event) =>
        this.allowed(event) &&
        this.x(event.at) >= start &&
        this.x(event.at) <= end,
    );
    const showConnections = $("#timeline-connections").checked;
    const selected = visible.find((event) => event.id === this.selected);
    const linked = selected
      ? [selected]
      : visible.filter((event) => event.kind === "message.created").slice(-70);
    if (showConnections)
      for (const event of linked) {
        if (!event.channel_id) continue;
        const origin = this.laneIndex.get(this.laneFor(event)),
          target = this.laneIndex.get(event.channel_id);
        if (origin === undefined || target === undefined) continue;
        const x = this.x(event.at),
          from = RULER + origin * ROW + ROW / 2,
          to = RULER + target * ROW + ROW / 2;
        connections.append(
          svg("path", {
            d: `M ${x} ${from} C ${x + 12} ${from - 20}, ${x + 12} ${to + 20}, ${x} ${to}`,
            fill: "none",
            stroke: color(event.agent_id, this.agents),
            "stroke-width": selected ? 2 : 1,
            opacity: selected ? 0.65 : 0.12,
            "marker-end": "url(#broadcast-arrow)",
          }),
        );
        connections.append(
          svg("circle", {
            cx: x,
            cy: to,
            r: selected ? 4 : 2,
            fill: color(event.agent_id, this.agents),
            opacity: selected ? 1 : 0.5,
          }),
        );
      }
    const collisions = new Map();
    for (const event of visible) {
      const lane = this.laneIndex.get(this.laneFor(event));
      if (lane === undefined) continue;
      const x = this.x(event.at),
        key = `${lane}:${Math.round(x / 17)}`,
        overlap = collisions.get(key) || 0;
      collisions.set(key, overlap + 1);
      const type = event.intervention
        ? "intervention"
        : event.kind.split(".")[0];
      const prior = existing.get(event.id);
      const node =
        prior || button("", (pointer) => this.activate(event, pointer));
      existing.delete(event.id);
      node.className =
        "time-event " +
        type +
        (event.position > this.cursor ? " future" : "") +
        (event.id === this.selected ? " selected" : "");
      node.dataset.eventId = event.id;
      node.dataset.position = event.position;
      node.style.left = x + "px";
      node.style.top =
        RULER + lane * ROW + ROW / 2 + ((overlap % 3) - 1) * 7 + "px";
      node.style.setProperty(
        "--event-color",
        event.intervention
          ? "#da8b40"
          : type === "tool"
            ? "#5285c9"
            : type === "memory"
              ? "#9173be"
              : color(event.agent_id, this.agents),
      );
      node.setAttribute(
        "aria-label",
        `${time(event.at)} ${this.agents[event.agent_id]?.name || "Human"} ${event.label}, event ${event.position}`,
      );
      node.title = `${time(event.at)} UTC · ${event.label}\n${event.preview}`;
      if (!prior) {
        node.append(
          el(
            "span",
            "event-symbol",
            type === "message"
              ? "▰"
              : type === "tool"
                ? "⌁"
                : type === "memory"
                  ? "◇"
                  : "⑂",
          ),
        );
        node.addEventListener("contextmenu", (pointer) => {
          pointer.preventDefault();
          this.activate(event, pointer, true);
        });
        node.addEventListener("keydown", (pointer) => {
          if (
            pointer.key === "ContextMenu" ||
            (pointer.shiftKey && pointer.key === "F10")
          ) {
            pointer.preventDefault();
            this.activate(event, pointer, true);
          }
        });
        root.append(node);
      }
    }
    for (const node of existing.values()) node.remove();
    if (this.branch?.parent_id) {
      const event = this.events.find(
        (event) => event.position === this.branch.fork_position,
      );
      if (event && this.x(event.at) >= start && this.x(event.at) <= end) {
        const mark = el("div", "fork-marker", "⑂ fork");
        mark.style.left = this.x(event.at) + "px";
        root.append(mark);
      }
    }
  }

  activate(event, pointer, context = false) {
    pointer.stopPropagation();
    this.selected = event.id;
    const rect = pointer.currentTarget.getBoundingClientRect();
    this.onEvent(
      event,
      { x: pointer.clientX || rect.left, y: pointer.clientY || rect.bottom },
      context,
    );
    this.drawEvents();
  }

  setCursor(cursor, reveal = false) {
    this.cursor = cursor;
    const event = this.events.find((event) => event.position === cursor);
    const x = event ? Math.max(LEFT + 20, this.x(event.at)) : LEFT + 20;
    $("#timeline-marker").style.left = x + "px";
    if (
      reveal &&
      (x < this.viewport.scrollLeft + LEFT + 25 ||
        x > this.viewport.scrollLeft + this.viewport.clientWidth - 30)
    )
      this.viewport.scrollLeft = Math.max(
        0,
        x - LEFT - (this.viewport.clientWidth - LEFT) * 0.65,
      );
    this.drawEvents();
  }

  seekAt(clientX) {
    const rect = this.viewport.getBoundingClientRect();
    if (clientX < rect.left + LEFT) return;
    const at =
      this.start +
      ((clientX - rect.left + this.viewport.scrollLeft - LEFT - 20) /
        this.scale) *
        60000;
    let low = 0,
      high = this.ordered.length;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (Date.parse(this.ordered[middle].at) <= at) low = middle + 1;
      else high = middle;
    }
    const event = this.ordered[Math.max(0, low - 1)];
    if (event) this.onSeek(event.position);
  }
}
