import {
  $,
  el,
  section,
  button,
  color,
  formatNumber,
  time,
  logoFor,
} from "./ui.js";

export function inspectAgent(agent, state, actions) {
  $("#inspector-type").textContent = "AGENT";
  const root = $("#inspector-body");
  root.replaceChildren();
  const avatar = el(
    "div",
    "profile-avatar",
    agent.name
      .split(/[ -]/)
      .filter(Boolean)
      .map((word) => word[0])
      .slice(0, 2)
      .join(""),
  );
  const logo = logoFor(agent);
  if (logo) {
    const image = el("img");
    image.src = logo;
    image.alt = "";
    avatar.replaceChildren(image);
  }
  avatar.style.color = color(agent.id, state.agents);
  root.append(
    avatar,
    el("h3", "profile-name", agent.name),
    el("p", "profile-model", agent.model || "Model not recorded"),
    el(
      "span",
      "status-tag",
      agent.active ? "Active in this branch" : "Removed from this branch",
    ),
  );
  const stats = el("div", "mini-stats");
  for (const [label, value] of [
    ["Messages", state.activity[agent.id] || 0],
    [
      "Memory slots",
      state.memories.filter((memory) => memory.owner_id === agent.id).length,
    ],
  ]) {
    const item = el("div", "mini-stat");
    item.append(el("strong", "", formatNumber(value)), el("span", "", label));
    stats.append(item);
  }
  root.append(stats);
  const prompt = section(
    "System prompt",
    agent.system_prompt ??
      "The source did not record this agent’s system prompt.",
  );
  prompt.append(button("Edit prompt", () => actions.editAgent(agent)));
  root.append(prompt);
  const memories = section("Memory at cursor");
  const found = state.memories.filter((memory) => memory.owner_id === agent.id);
  if (!found.length)
    memories.append(el("p", "", "No memory observed at this cursor."));
  for (const memory of found) {
    memories.append(
      el("div", "memory-preview", memory.preview + "…"),
      button("Read memory", () => actions.readMemory(memory)),
    );
  }
  root.append(memories);
  const manage = section("Branch controls");
  manage.append(
    button("Edit agent", () => actions.editAgent(agent)),
    button(
      agent.active ? "Remove agent" : "Restore agent",
      () => actions.removeAgent(agent),
      agent.active ? "danger" : "",
    ),
  );
  root.append(manage);
  root.append(
    el(
      "p",
      "source-note",
      "Changes are recorded as interventions. New behavior requires an execution runtime supplied by the application.",
    ),
  );
}

export function inspectEnvironment(state, actions) {
  $("#inspector-type").textContent = "ENVIRONMENT";
  const root = $("#inspector-body");
  root.replaceChildren();
  root.append(
    el("div", "profile-avatar", "E"),
    el("h3", "profile-name", state.environment.task || "Run environment"),
  );
  root.append(
    section(
      "Shared goal",
      state.environment.goal || "No goal recorded at this cursor.",
    ),
  );
  const selection = state.environment.metadata.selection;
  if (selection) root.append(section("Selection boundary", selection));
  const capabilities = section(
    "Execution",
    "This example reconstructs recorded observations. Register a runtime adapter to generate new continuations.",
  );
  capabilities.append(button("Edit goal", actions.editGoal));
  root.append(capabilities);
  root.append(
    section(
      "Explore",
      "Select an agent in the graph or an event in the feed to inspect its context, memory, and recorded model output.",
    ),
  );
}

export function inspectEvent(event, state, actions) {
  $("#inspector-type").textContent =
    event.source.origin === "intervention" ? "INTERVENTION" : "EVENT";
  const root = $("#inspector-body");
  root.replaceChildren();
  const data = event.data;
  const aid =
      data.sender_id ||
      data.agent_id ||
      data.owner_id ||
      (event.kind.startsWith("agent.") ? data.id : null),
    agent = state.agents[aid];
  root.append(
    el(
      "span",
      "eyebrow",
      `${time(event.occurred_at)} UTC · EVENT ${event.position}`,
    ),
    el("h3", "profile-name", agent?.name || data.sender_name || "Environment"),
    el("p", "profile-model", event.kind),
  );
  if (agent)
    root.append(
      button("Inspect agent", () => actions.selectAgent(agent.id), "subtle"),
    );
  root.append(
    el(
      "div",
      "content-text",
      data.content ||
        data.goal ||
        data.tool_name ||
        JSON.stringify(data, null, 2),
    ),
  );
  if (data.arguments) {
    const args = section("Arguments");
    args.append(
      el("pre", "metadata-box", JSON.stringify(data.arguments, null, 2)),
    );
    root.append(args);
  }
  if (Object.hasOwn(data, "result"))
    root.append(
      section(
        "Tool result",
        data.result === null
          ? "No output recorded."
          : typeof data.result === "string"
            ? data.result
            : JSON.stringify(data.result, null, 2),
      ),
    );
  if (data.error) root.append(section("Tool error", data.error));
  if (data.metadata?.session_goal)
    root.append(section("Session goal", data.metadata.session_goal));
  if (data.metadata?.model_output_artifact) {
    const raw = section("Model response");
    raw.append(
      button("Open recorded response", () =>
        actions.readArtifact(data.metadata.model_output_artifact),
      ),
    );
    root.append(raw);
  }
  const provenance = section("Source reference");
  provenance.append(
    el("pre", "metadata-box", JSON.stringify(event.source, null, 2)),
  );
  root.append(provenance);
}

export function inspectAnalysis(record, state) {
  $("#inspector-type").textContent = "PLUGIN";
  const root = $("#inspector-body");
  root.replaceChildren();
  root.append(
    el("h3", "profile-name", "Activity analysis"),
    el(
      "p",
      "profile-model",
      `${record.plugin_id} v${record.plugin_version} · cursor ${record.cursor}`,
    ),
  );
  const output = record.output;
  for (const [label, key] of [
    ["Messages", "messages"],
    ["Tool calls", "tool_calls"],
    ["Memory slots", "memory_items"],
    ["Active agents", "active_agents"],
    ["Tool errors", "tool_errors"],
  ]) {
    const row = el("div", "analysis-row");
    row.append(
      el("span", "", label),
      el("strong", "", formatNumber(output[key])),
    );
    root.append(row);
  }
  root.append(section("Participation"));
  const entries = Object.entries(output.messages_by_agent).sort(
    (a, b) => b[1] - a[1],
  );
  for (const [id, count] of entries) {
    const row = el("div", "analysis-row");
    row.append(
      el("span", "", state.agents[id]?.name || id),
      el("strong", "", count),
    );
    const track = el("div", "analysis-bar"),
      bar = el("div");
    bar.style.width =
      (count / Math.max(1, ...entries.map((x) => x[1]))) * 100 + "%";
    track.append(bar);
    root.append(row, track);
  }
  root.append(el("p", "source-note", output.interpretation));
}
