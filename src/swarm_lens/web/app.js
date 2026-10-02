import {
  $,
  $$,
  el,
  button,
  api,
  post,
  toast,
  failure,
  time,
  date,
  formatNumber,
  color,
  logoFor,
} from "./ui.js";
import { renderGraph } from "./graph.js";
import { EventTimeline } from "./timeline.js";
import { field, openDialog } from "./dialog.js";
import {
  inspectAgent,
  inspectEnvironment,
  inspectEvent,
  inspectAnalysis,
} from "./inspect.js";

const view = {
  workspace: null,
  run: null,
  branch: null,
  events: [],
  state: null,
  cursor: 0,
  agent: null,
  event: null,
  filter: "messages",
  query: "",
  limit: 45,
  playing: false,
};
let requestId = 0,
  seekTimer,
  playTimer;
const actions = {
  selectAgent,
  editAgent,
  removeAgent,
  readMemory,
  readArtifact,
  editGoal,
};
const timeline = new EventTimeline({
  onSeek: (position) => {
    stopPlayback();
    clearTimeout(seekTimer);
    seekTimer = setTimeout(() => seek(position), 45);
  },
  onAgent: selectAgent,
  onEvent: timelineEvent,
});

async function refreshWorkspace() {
  view.workspace = await api("/workspace");
}

function renderBranches() {
  const root = $("#branches");
  root.replaceChildren();
  const branches = view.workspace.branches.filter(
    (branch) => branch.run_id === view.run.id,
  );
  const depth = (branch) => {
    let count = 0;
    while (branch.parent_id) {
      count++;
      branch = branches.find((parent) => parent.id === branch.parent_id);
      if (!branch) break;
    }
    return count;
  };
  for (const branch of branches) {
    const node = button(
      "",
      () => loadBranch(branch.id).catch(failure),
      "branch" + (branch.id === view.branch.id ? " active" : ""),
    );
    node.style.paddingLeft = 9 + Math.min(depth(branch), 4) * 10 + "px";
    node.setAttribute(
      "aria-current",
      branch.id === view.branch.id ? "true" : "false",
    );
    node.append(
      el("span", "branch-name", (branch.parent_id ? "⑂ " : "◉ ") + branch.name),
      el(
        "span",
        "",
        branch.parent_id
          ? `Forked at ${formatNumber(branch.fork_position)} · ${formatNumber(branch.head - branch.fork_position)} new`
          : `${formatNumber(branch.head)} recorded events`,
      ),
    );
    root.append(node);
  }
  $("#compare").disabled = branches.length < 2;
}

async function loadBranch(id, cursor) {
  stopPlayback();
  $("#footer-status").textContent = "Loading branch history…";
  const data = await api(`/branches/${id}/timeline`);
  view.branch = data.branch;
  view.events = data.events;
  view.run = view.workspace.runs.find((run) => run.id === view.branch.run_id);
  view.agent = null;
  view.event = null;
  view.limit = 45;
  $("#run-select").value = view.run.id;
  $("#branch-name").textContent = view.branch.name;
  $("#branch-status").textContent = view.branch.parent_id
    ? "Forked trajectory"
    : "Observed history";
  $("#context-title").textContent = view.branch.parent_id
    ? "Branch state"
    : "Recorded trajectory";
  $("#dataset-link").href = view.run.metadata.dataset_url || "#";
  $("#dataset-link").hidden = !view.run.metadata.dataset_url;
  $("#attribution").textContent =
    view.run.metadata.attribution ||
    view.run.metadata.repository ||
    "Source application";
  $("#source-date").textContent = date(view.run.metadata.start) + " · UTC";
  $("#cursor").max = view.branch.head;
  renderBranches();
  timeline.setData(view.events, view.branch);
  await seek(cursor ?? view.branch.head);
}

async function seek(cursor) {
  cursor = Math.max(0, Math.min(view.branch.head, Number(cursor)));
  view.cursor = cursor;
  $("#cursor").value = cursor;
  $("#position-label").textContent =
    `${formatNumber(cursor)} / ${formatNumber(view.branch.head)}`;
  timeline.setCursor(cursor, true);
  const ticket = ++requestId;
  try {
    const state = await api(
      `/branches/${view.branch.id}/state?cursor=${cursor}`,
    );
    if (ticket !== requestId) return;
    view.state = state;
    if (view.event && view.event.position > cursor) view.event = null;
    if (view.agent && !state.agents[view.agent]) view.agent = null;
    $("#task-name").textContent = state.environment.task || view.run.name;
    $("#task-description").textContent =
      state.environment.goal || "Explore recorded swarm state.";
    $("#cursor-time").textContent =
      `${date(state.occurred_at)} · ${time(state.occurred_at)} UTC`;
    $("#counts").replaceChildren(
      ...[
        ["agents", "agents"],
        ["messages", "messages"],
        ["tools", "tool calls"],
        ["memories", "memory slots"],
      ].map(([key, label]) => {
        const node = el("span");
        node.append(
          el("strong", "", formatNumber(state.counts[key])),
          document.createTextNode(label),
        );
        return node;
      }),
    );
    renderGraph(state, view.agent, selectAgent);
    renderFeed();
    await renderInspector();
    $("#footer-status").textContent =
      `State reconstructed at event ${formatNumber(cursor)} · ${view.branch.parent_id ? "Changes belong to this branch" : "Original history preserved"}`;
    $("#previous").disabled = cursor === 0;
    $("#next").disabled = cursor === view.branch.head;
  } catch (error) {
    if (ticket === requestId) failure(error);
  }
}

function matches(event) {
  if (event.position > view.cursor) return false;
  if (view.agent && event.agent_id !== view.agent) return false;
  if (view.filter === "messages" && !event.kind.startsWith("message."))
    return false;
  if (view.filter === "tools" && !event.kind.startsWith("tool.")) return false;
  if (view.filter === "memory" && !event.kind.startsWith("memory."))
    return false;
  const agent = view.state.agents[event.agent_id];
  return (
    !view.query ||
    `${event.preview} ${agent?.name || ""} ${event.label}`
      .toLowerCase()
      .includes(view.query)
  );
}

function renderFeed() {
  const all = view.events.filter(matches),
    visible = all.slice(-view.limit).reverse(),
    root = $("#feed");
  root.replaceChildren();
  $("#feed-label").textContent = view.agent
    ? view.state.agents[view.agent]?.name || "Agent"
    : view.filter === "messages"
      ? "# " +
        Object.values(view.state.channels)
          .map((channel) => channel.name)
          .join(" · # ")
      : view.filter === "tools"
        ? "Recorded tool activity"
        : view.filter === "memory"
          ? "Memory revisions"
          : "Event history";
  $("#feed-count").textContent = `${formatNumber(all.length)} at cursor`;
  $("#clear-agent").hidden = !view.agent;
  if (!visible.length) {
    root.append(
      el(
        "p",
        "empty",
        "No matching events at this point. Move the timeline forward or clear your filters.",
      ),
    );
  }
  for (const event of visible) {
    const node = button(
      "",
      () => selectEvent(event).catch(failure),
      "event-row" + (view.event?.id === event.id ? " selected" : ""),
    );
    const body = el("div"),
      title = el("div", "event-title"),
      dot = el("span", "event-avatar");
    dot.style.background = event.intervention
      ? "#da8b40"
      : color(event.agent_id, view.state.agents);
    const logo = logoFor(view.state.agents[event.agent_id]);
    if (logo) {
      dot.classList.add("logo");
      const image = el("img");
      image.src = logo;
      image.alt = "";
      dot.append(image);
    }
    title.append(
      dot,
      el(
        "span",
        "",
        view.state.agents[event.agent_id]?.name ||
          (event.kind === "message.created" ? "Human" : "Environment"),
      ),
      el(
        "span",
        "event-type",
        event.intervention ? "Intervention" : event.kind.split(".")[0],
      ),
    );
    body.append(
      title,
      el("div", "event-preview", event.preview || event.label),
    );
    node.append(el("span", "event-time", time(event.at)), body);
    root.append(node);
  }
  $("#more-events").hidden = all.length <= view.limit;
}

async function renderInspector() {
  if (view.event) {
    const id = view.event.id,
      branch = view.branch.id,
      cursor = view.cursor;
    const detail = await api(
      `/branches/${branch}/events/${id}?cursor=${cursor}`,
    );
    if (
      view.event?.id === id &&
      view.branch.id === branch &&
      view.cursor === cursor
    )
      inspectEvent(detail, view.state, actions);
  } else if (view.agent && view.state.agents[view.agent])
    inspectAgent(view.state.agents[view.agent], view.state, actions);
  else inspectEnvironment(view.state, actions);
}

function selectAgent(id) {
  if (!view.state.agents[id]) {
    toast("This agent is not present at the selected cursor.");
    return;
  }
  view.agent = id;
  view.event = null;
  view.limit = 45;
  renderGraph(view.state, id, selectAgent);
  renderFeed();
  renderInspector().catch(failure);
}
async function selectEvent(event) {
  view.event = event;
  renderFeed();
  await renderInspector();
}

function stopPlayback() {
  view.playing = false;
  clearTimeout(playTimer);
  $("#play").textContent = "▶";
  $("#play").setAttribute("aria-label", "Play timeline");
}
async function playStep() {
  if (!view.playing) return;
  if (view.cursor >= view.branch.head) {
    stopPlayback();
    return;
  }
  await seek(view.cursor + Number($("#speed").value));
  if (view.playing) playTimer = setTimeout(playStep, 450);
}

function forkDialog() {
  $("#event-menu").hidden = true;
  stopPlayback();
  const content = el("div");
  content.append(
    el(
      "p",
      "",
      `Create a branch from event ${formatNumber(view.cursor)}. It will inherit history only up to this point.`,
    ),
  );
  const name = field(
    "Branch name",
    "name",
    "Experiment " +
      view.workspace.branches.filter((b) => b.run_id === view.run.id).length,
  );
  name.input.required = true;
  content.append(name.fragment);
  const source = view.branch.id,
    cursor = view.cursor;
  openDialog("Fork this moment", content, {
    confirm: "Create branch",
    submit: async (form) => {
      const branch = await post(`/branches/${source}/fork`, {
        cursor,
        name: form.get("name"),
      });
      await refreshWorkspace();
      await loadBranch(branch.id);
      toast("Branch created. The original trajectory is preserved.");
    },
  });
}

function interventionDialog(title, fields, kind, buildData) {
  $("#event-menu").hidden = true;
  stopPlayback();
  const content = el("div"),
    source = view.branch.id,
    cursor = view.cursor;
  const needsFork = !view.branch.parent_id || cursor !== view.branch.head;
  content.append(
    el(
      "p",
      "",
      needsFork
        ? "This change will be saved on a new branch at the selected moment."
        : "This change will append an intervention to the current branch.",
    ),
  );
  if (needsFork) {
    const name = field("New branch name", "branch_name", title + " experiment");
    name.input.required = true;
    content.append(name.fragment);
  }
  fields.forEach((item) => content.append(item.fragment));
  content.append(
    el(
      "p",
      "",
      "This updates branch state. Recorded messages stay unchanged; new responses require a runtime adapter.",
    ),
  );
  let target = source;
  openDialog(title, content, {
    confirm: needsFork ? "Fork & apply" : "Apply change",
    submit: async (form) => {
      if (needsFork && target === source)
        target = (
          await post(`/branches/${source}/fork`, {
            cursor,
            name: form.get("branch_name"),
          })
        ).id;
      await post(`/branches/${target}/interventions`, {
        kind,
        data: buildData(form),
        expected_head: cursor,
      });
      await refreshWorkspace();
      await loadBranch(target);
      toast("Intervention saved to branch history.");
    },
  });
}

function editAgent(agent) {
  const name = field("Agent name", "name", agent.name);
  name.input.required = true;
  interventionDialog(
    "Edit agent",
    [
      name,
      field("Model identifier", "model", agent.model || ""),
      field("System prompt", "prompt", agent.system_prompt || "", "textarea"),
    ],
    "agent.updated",
    (form) => ({
      id: agent.id,
      name: form.get("name"),
      model: form.get("model") || null,
      system_prompt: form.get("prompt"),
    }),
  );
}
function addAgent() {
  const name = field("Agent name", "name", "New agent");
  name.input.required = true;
  interventionDialog(
    "Add agent",
    [
      name,
      field("Model identifier", "model"),
      field("System prompt", "prompt", "", "textarea"),
    ],
    "agent.added",
    (form) => ({
      id: crypto.randomUUID(),
      name: form.get("name"),
      model: form.get("model") || null,
      system_prompt: form.get("prompt"),
    }),
  );
}
function removeAgent(agent) {
  interventionDialog(
    agent.active ? "Remove agent" : "Restore agent",
    [],
    agent.active ? "agent.removed" : "agent.updated",
    () => (agent.active ? { id: agent.id } : { id: agent.id, active: true }),
  );
}
function editGoal() {
  interventionDialog(
    "Change goal",
    [field("Shared goal", "goal", view.state.environment.goal, "textarea")],
    "environment.updated",
    (form) => ({ goal: form.get("goal") }),
  );
}

async function readMemory(memory) {
  try {
    const value = await api(
      `/branches/${view.branch.id}/memory?cursor=${view.cursor}&memory_id=${encodeURIComponent(memory.id)}`,
    );
    const text = el("div", "content-text", value.content);
    openDialog("Recorded memory", text, { kicker: "MEMORY AT CURSOR" });
  } catch (error) {
    failure(error);
  }
}
async function readArtifact(digest) {
  try {
    const value = await api(`/artifacts/${digest}`),
      text = el("pre", "metadata-box", JSON.stringify(value, null, 2));
    text.style.maxHeight = "55vh";
    openDialog("Recorded model response", text, { kicker: "SOURCE ARTIFACT" });
  } catch (error) {
    failure(error);
  }
}

function compareDialog() {
  stopPlayback();
  const content = el("div"),
    branches = view.workspace.branches.filter(
      (branch) => branch.run_id === view.run.id && branch.id !== view.branch.id,
    );
  const choice = field("Reference branch", "reference", "", "select");
  branches.forEach((branch) => {
    const option = el("option", "", branch.name);
    option.value = branch.id;
    choice.input.append(option);
  });
  choice.input.value = view.branch.parent_id || branches[0]?.id;
  content.append(
    el(
      "p",
      "",
      `Current branch: ${view.branch.name}, event ${view.cursor}. A parent branch is compared at the fork point; other branches at the nearest available cursor.`,
    ),
    choice.fragment,
  );
  const result = el("div");
  content.append(result);
  const update = async () => {
    const reference = branches.find(
      (branch) => branch.id === choice.input.value,
    );
    if (!reference) return;
    const cursor =
      reference.id === view.branch.parent_id
        ? view.branch.fork_position
        : Math.min(view.cursor, reference.head);
    const diff = await api(
      `/compare?left=${reference.id}&right=${view.branch.id}&left_cursor=${cursor}&right_cursor=${view.cursor}`,
    );
    result.replaceChildren();
    let changes = 0;
    for (const group of [
      "agents",
      "channels",
      "messages",
      "memories",
      "tools",
    ]) {
      const entry = diff[group];
      if (entry.added.length || entry.removed.length || entry.changed.length) {
        changes++;
        result.append(
          el(
            "div",
            "diff-change",
            `${group}: ${entry.added.length} added · ${entry.removed.length} removed · ${entry.changed.length} changed`,
          ),
        );
      }
      if (group === "agents" || group === "channels")
        for (const change of entry.changed) {
          const block = el("div", "diff-change");
          block.append(el("strong", "", change.after.name || change.id));
          for (const key of Object.keys(change.after)) {
            if (
              JSON.stringify(change.before[key]) !==
              JSON.stringify(change.after[key])
            )
              block.append(
                el(
                  "pre",
                  "",
                  `${key}\nBefore: ${JSON.stringify(change.before[key])}\nAfter: ${JSON.stringify(change.after[key])}`,
                ),
              );
          }
          result.append(block);
        }
    }
    if (
      JSON.stringify(diff.environment.before) !==
      JSON.stringify(diff.environment.after)
    ) {
      changes++;
      const block = el("div", "diff-change");
      block.append(
        el("strong", "", "Environment changed"),
        el(
          "pre",
          "",
          `Before: ${diff.environment.before.goal}\nAfter: ${diff.environment.after.goal}`,
        ),
      );
      result.append(block);
    }
    if (!changes)
      result.append(
        el("p", "", "The reconstructed states are identical at these cursors."),
      );
  };
  choice.input.onchange = () => update().catch(failure);
  openDialog("Compare trajectories", content, { kicker: "BRANCH DIFF" });
  update().catch(failure);
}

$("#fork-button").onclick = forkDialog;
$("#new-branch").onclick = forkDialog;
$("#cursor").addEventListener("input", (event) => {
  stopPlayback();
  clearTimeout(seekTimer);
  seekTimer = setTimeout(() => seek(Number(event.target.value)), 70);
});
$("#previous").onclick = () => {
  stopPlayback();
  seek(view.cursor - 1);
};
$("#next").onclick = () => {
  stopPlayback();
  seek(view.cursor + 1);
};
$("#go-end").onclick = () => {
  stopPlayback();
  seek(view.branch.head);
};
$("#play").onclick = () => {
  if (view.playing) {
    stopPlayback();
    return;
  }
  view.playing = true;
  $("#play").textContent = "Ⅱ";
  $("#play").setAttribute("aria-label", "Pause timeline");
  if (view.cursor >= view.branch.head) view.cursor = 0;
  playStep();
};
$$("[data-filter]").forEach(
  (tab) =>
    (tab.onclick = () => {
      view.filter = tab.dataset.filter;
      view.limit = 45;
      $$("[data-filter]").forEach((other) => {
        other.classList.toggle("active", other === tab);
        other.setAttribute("aria-selected", String(other === tab));
      });
      renderFeed();
    }),
);
$("#search").oninput = (event) => {
  view.query = event.target.value.trim().toLowerCase();
  renderFeed();
};
$("#more-events").onclick = () => {
  view.limit += 45;
  renderFeed();
};
$("#clear-agent").onclick = () => {
  view.agent = null;
  view.event = null;
  renderGraph(view.state, null, selectAgent);
  renderFeed();
  renderInspector().catch(failure);
};
$("#add-agent").onclick = addAgent;
$("#edit-goal").onclick = editGoal;
$("#compare").onclick = compareDialog;
$("#analyze").onclick = async () => {
  try {
    stopPlayback();
    const result = await post(`/branches/${view.branch.id}/analyses`, {
      plugin_id: "activity",
      cursor: view.cursor,
    });
    view.event = null;
    inspectAnalysis(result, view.state);
    toast("Analysis saved with its input history and plugin version.");
  } catch (error) {
    failure(error);
  }
};
$("#checkpoint").onclick = async () => {
  try {
    const result = await post(`/branches/${view.branch.id}/checkpoint`, {
      cursor: view.cursor,
    });
    toast(`Git checkpoint saved · ${result.commit.slice(0, 10)}`);
  } catch (error) {
    failure(error);
  }
};
$("#run-select").onchange = (event) => {
  const branch = view.workspace.branches.find(
    (branch) => branch.run_id === event.target.value && !branch.parent_id,
  );
  if (branch) loadBranch(branch.id).catch(failure);
};

const resize = $("#resize-inspector");
resize.onpointerdown = (event) => {
  resize.setPointerCapture(event.pointerId);
};
resize.onpointermove = (event) => {
  if (resize.hasPointerCapture(event.pointerId))
    document.documentElement.style.setProperty(
      "--inspector",
      Math.max(240, Math.min(600, window.innerWidth - event.clientX)) + "px",
    );
};
resize.onkeydown = (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const width = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue("--inspector"),
  );
  document.documentElement.style.setProperty(
    "--inspector",
    Math.max(
      240,
      Math.min(600, width + (event.key === "ArrowLeft" ? 20 : -20)),
    ) + "px",
  );
};
new ResizeObserver(() => {
  if (view.state) renderGraph(view.state, view.agent, selectAgent);
}).observe($("#graph"));

async function timelineEvent(event, point, context) {
  stopPlayback();
  view.event = event;
  await seek(event.position);
  if (view.cursor !== event.position) return;
  const menu = $("#event-menu"),
    agent = view.state.agents[event.agent_id];
  menu.replaceChildren();
  const header = el("div", "event-menu-header"),
    logo = logoFor(agent);
  if (logo) {
    const image = el("img");
    image.src = logo;
    image.alt = "";
    header.append(image);
  }
  header.append(
    el(
      "strong",
      "",
      agent?.name ||
        (event.kind === "message.created" ? "Human" : "Environment"),
    ),
  );
  menu.append(
    header,
    el("time", "", `${time(event.at)} UTC · event ${event.position}`),
    el("p", "", event.preview || event.label),
  );
  const options = el("div", "menu-actions");
  const item = (label, action, className = "") =>
    options.append(
      button(
        label,
        () => {
          menu.hidden = true;
          action();
        },
        className,
      ),
    );
  item("Inspect event", () => {
    view.event = event;
    renderInspector().catch(failure);
    $(".inspector").scrollIntoView({ block: "nearest" });
  });
  item("⑂  Create branch here", forkDialog, "menu-primary");
  if (agent) {
    item("Change agent prompt…", () => editAgent(agent));
    item(agent.active ? "Remove this agent…" : "Restore this agent…", () =>
      removeAgent(agent),
    );
  }
  item("Add an agent…", addAgent);
  item("Change shared goal…", editGoal);
  menu.append(options);
  menu.hidden = false;
  menu.style.left =
    Math.max(8, Math.min(point.x, window.innerWidth - menu.offsetWidth - 12)) +
    "px";
  menu.style.top =
    Math.max(
      8,
      Math.min(point.y + 10, window.innerHeight - menu.offsetHeight - 12),
    ) + "px";
  if (context) options.querySelector("button").focus();
}
document.addEventListener("pointerdown", (event) => {
  if (
    !event.target.closest("#event-menu") &&
    !event.target.closest(".time-event")
  )
    $("#event-menu").hidden = true;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("#event-menu").hidden = true;
});

async function boot() {
  await refreshWorkspace();
  if (!view.workspace.runs.length) {
    $("#task-name").textContent = "No runs yet";
    $("#footer-status").textContent =
      "Build an application with the framework and supply its first run.";
    return;
  }
  const picker = $("#run-select");
  picker.replaceChildren(
    ...view.workspace.runs.map((run) => {
      const option = el("option", "", run.name);
      option.value = run.id;
      return option;
    }),
  );
  $("#checkpoint").disabled = !view.workspace.capabilities.git;
  await loadBranch(
    view.workspace.branches.find((branch) => !branch.parent_id).id,
  );
}
boot().catch(failure);
