import { svg, color, formatNumber } from "./ui.js";

const WIDTH = 360;
const ROW = 26;
const PAD = 12;

function rowY(index) {
  return PAD + ROW * index + ROW / 2;
}

// Agents on the left, channels on the right, one curve per agent-channel membership.
export function topologyGraph(state, onSelect) {
  const agents = Object.values(state.agents);
  const channels = Object.values(state.channels);
  const height = PAD * 2 + ROW * Math.max(agents.length, channels.length);
  const root = svg("svg", { class: "ins-graph", viewBox: `0 0 ${WIDTH} ${height}`, role: "img", "aria-label": "Agent and channel topology" });
  const agentY = new Map(agents.map((agent, index) => [agent.id, rowY(index)]));
  const channelY = new Map(channels.map((channel, index) => [channel.id, rowY(index)]));
  const maxCount = Math.max(1, ...state.edges.map((edge) => edge.count));
  for (const edge of state.edges) {
    const from = agentY.get(edge.agent_id);
    const to = channelY.get(edge.channel_id);
    if (from === undefined || to === undefined) continue;
    root.append(svg("path", {
      class: "ins-graph-link",
      d: `M 150 ${from} C 190 ${from}, 190 ${to}, 230 ${to}`,
      "stroke-width": 1 + (2 * edge.count) / maxCount,
    }));
  }
  agents.forEach((agent) => root.append(agentNode(agent, agentY.get(agent.id), state, onSelect)));
  channels.forEach((channel) => {
    const y = channelY.get(channel.id);
    root.append(
      svg("circle", { class: "ins-graph-channel-dot", cx: 236, cy: y, r: 3 }),
      svg("text", { class: "ins-graph-channel", x: 246, y: y + 4 }, `#${channel.name}`),
    );
  });
  return root;
}

function agentNode(agent, y, state, onSelect) {
  const group = svg("g", {
    class: `ins-graph-agent${agent.active ? "" : " inactive"}`,
    role: "button",
    tabindex: "0",
    "aria-label": `Inspect ${agent.name}`,
  });
  group.append(
    svg("title", {}, `${agent.name} · ${formatNumber(state.activity[agent.id] || 0)} messages`),
    svg("rect", { class: "ins-graph-hit", x: 0, y: y - ROW / 2, width: 150, height: ROW, rx: 6 }),
    svg("circle", { cx: 14, cy: y, r: 4, fill: color(agent.id, state.agents) }),
    svg("text", { x: 26, y: y + 4 }, agent.name.length > 18 ? agent.name.slice(0, 17) + "…" : agent.name),
    svg("circle", { class: "ins-graph-channel-dot", cx: 150, cy: y, r: 2.5 }),
  );
  group.addEventListener("click", () => onSelect(agent.id));
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(agent.id);
    }
  });
  return group;
}
