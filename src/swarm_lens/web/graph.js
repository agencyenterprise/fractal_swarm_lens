import { $, color, svg, formatNumber, logoFor } from "./ui.js";

export function renderGraph(state, selected, onSelect) {
  const graph = $("#graph"),
    width = Math.max(graph.clientWidth, 280),
    height = Math.max(graph.clientHeight, 180);
  graph.setAttribute("viewBox", `0 0 ${width} ${height}`);
  graph.replaceChildren();
  const agents = Object.values(state.agents),
    channels = Object.values(state.channels);
  if (!agents.length) {
    graph.append(
      svg(
        "text",
        {
          x: width / 2,
          y: height / 2,
          "text-anchor": "middle",
          fill: "#8195a3",
          "font-size": 12,
        },
        "Advance the timeline to reveal the swarm.",
      ),
    );
    return;
  }
  const compact = width < 440,
    cols = Math.ceil(agents.length / 2),
    positions = new Map();
  agents.forEach((agent, index) =>
    positions.set(agent.id, {
      x: compact
        ? width * (index < cols ? 0.2 : 0.8)
        : (width * ((index % cols) + 0.5)) / cols,
      y: compact
        ? 20 + ((height - 50) * (index % cols)) / Math.max(1, cols - 1)
        : index < cols
          ? height * 0.17
          : height * 0.73,
    }),
  );
  const channelPositions = new Map(
    channels.map((channel, index) => [
      channel.id,
      {
        x: (width * (index + 0.5)) / Math.max(1, channels.length),
        y: height * 0.48,
      },
    ]),
  );
  for (const edge of state.edges) {
    const from = positions.get(edge.agent_id),
      to = channelPositions.get(edge.channel_id);
    if (!from || !to) continue;
    graph.append(
      svg("path", {
        d: `M ${from.x} ${from.y} C ${from.x} ${to.y}, ${to.x} ${from.y}, ${to.x} ${to.y}`,
        class: "graph-link",
        opacity: selected && selected !== edge.agent_id ? 0.18 : 0.65,
        "stroke-width": Math.min(3, 0.6 + Math.log1p(edge.count) / 3),
      }),
    );
  }
  for (const channel of channels) {
    const point = channelPositions.get(channel.id),
      group = svg("g", { class: "graph-channel" });
    group.append(
      svg("rect", {
        x: point.x - (compact ? 34 : 57),
        y: point.y - 15,
        width: compact ? 68 : 114,
        height: 30,
        rx: 6,
      }),
      svg(
        "text",
        { x: point.x, y: point.y + 4, "text-anchor": "middle" },
        "# " + channel.name,
      ),
    );
    graph.append(group);
  }
  for (const agent of agents) {
    const point = positions.get(agent.id),
      group = svg("g", {
        class: `graph-agent ${agent.active ? "" : "inactive"} ${selected === agent.id ? "selected" : ""}`,
        role: "button",
        tabindex: "0",
        "aria-label": `Inspect ${agent.name}`,
      });
    const ink = color(agent.id, state.agents),
      label =
        width < 540
          ? agent.name.replace("Claude ", "").replace("Gemini ", "G. ")
          : agent.name;
    group.append(
      svg(
        "title",
        {},
        `${agent.name} · ${formatNumber(state.activity[agent.id] || 0)} messages`,
      ),
      svg("circle", {
        cx: point.x,
        cy: point.y,
        r: compact ? 12 : 16,
        fill: "#fff",
        stroke: "#d7e4e7",
      }),
      svg("circle", { cx: point.x, cy: point.y, r: 5, fill: ink }),
      svg(
        "text",
        {
          x: point.x,
          y: point.y + (compact ? 23 : 32),
          "text-anchor": "middle",
          "font-size": width < 540 ? 9 : 11,
          style: compact ? "font-size:8px" : "",
        },
        label,
      ),
      ...(compact
        ? []
        : [
            svg(
              "text",
              {
                x: point.x,
                y: point.y + 46,
                "text-anchor": "middle",
                class: "agent-count",
              },
              agent.active
                ? `${formatNumber(state.activity[agent.id] || 0)} messages`
                : "Removed",
            ),
          ]),
    );
    const logo = logoFor(agent);
    if (logo)
      group.append(
        svg("image", {
          href: logo,
          x: point.x - 9,
          y: point.y - 9,
          width: 18,
          height: 18,
        }),
      );
    group.addEventListener("click", () => onSelect(agent.id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect(agent.id);
      }
    });
    graph.append(group);
  }
}
