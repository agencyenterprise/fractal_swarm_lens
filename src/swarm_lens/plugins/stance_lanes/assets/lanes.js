// Renders a stance-lanes report: one lane per agent, one cell per message, flip arrows from the peer
// message the agent read, holdout badges, the run pattern, and per-flip actions (open, fork before).
import { el, svg, button, colors, tip } from "swarm-lens/ui.js";

export const CELL = 30, GAP = 6, COLUMN_GAP = 16, LABEL = 132;
const PATTERN_TIPS = {
  unanimous: "Every agent held the same stance throughout",
  stalemate: "Agents disagreed and nobody changed stance",
  cascade: "Everyone ended on a stance that a strict minority held in its first message",
  "dissenter gives in": "Everyone ended on the stance the majority held in its first message",
  "split resolved": "Agents started evenly split and everyone ended on one side's stance",
  wavered: "Everyone started on one stance, some left it, and all came back",
  "new consensus": "Everyone converged on a stance nobody held at first",
  mixed: "Stances changed but the agents did not converge",
  "no stances": "No message stated a stance",
};

const left = (column) => column * (CELL + COLUMN_GAP);
const top = (row) => row * (CELL + GAP);

// An arrow from the message read to the flip. Messages in the same column (sequential turns of one round)
// get an arc through the gap right of the column, so it never crosses the cell between them; other pairs
// get a straight segment between the cells' edges.
export function arrowPath(from, to) {
  if (from.column === to.column) {
    const x = left(to.column) + CELL, bulge = 4 + 4 * Math.abs(to.row - from.row);
    const y1 = top(from.row) + CELL / 2, y2 = top(to.row) + CELL / 2;
    return `M${x},${y1} Q${x + 2 * bulge},${(y1 + y2) / 2} ${x + 1},${y2}`;
  }
  const x1 = left(from.column) + CELL / 2, y1 = top(from.row) + CELL / 2;
  const x2 = left(to.column) + CELL / 2, y2 = top(to.row) + CELL / 2;
  const length = Math.hypot(x2 - x1, y2 - y1), trim = CELL / 2 + 2;
  const dx = (x2 - x1) / length * trim, dy = (y2 - y1) / length * trim;
  return `M${x1 + dx},${y1 + dy} L${x2 - dx},${y2 - dy}`;
}

export function stanceColor(report, stance) {
  const index = report.stances.indexOf(stance);
  return index < 0 ? "var(--surface-2)" : colors[index % colors.length];
}

// Lane coordinates of every message, so an arrow can start at the exact message that was read.
export function cellIndex(report) {
  const where = new Map();
  report.lanes.forEach((lane, row) => lane.cells.forEach((cell, column) => where.set(cell.event_id, { row, column })));
  return where;
}

export function arrows(report) {
  const where = cellIndex(report);
  return report.flips.flatMap((flip) => Object.keys(flip.toward).map((peer) => {
    const from = where.get(flip.read[peer].event_id), to = where.get(flip.event_id);
    return from && to ? { from, to, peer, flip, credit: flip.toward[peer] } : null;
  }).filter(Boolean));
}

function header(report) {
  const head = el("div", "stance-head");
  const pattern = report.typology;
  const chip = tip(el("span", `badge ${pattern.kind === "cascade" ? "warn" : ""}`.trim(),
    pattern.kind + (pattern.agents.length && !["unanimous", "stalemate", "mixed", "wavered"].includes(pattern.kind)
      ? ` · ${pattern.agents.join(", ")}` : "")), PATTERN_TIPS[pattern.kind] || "");
  head.append(el("h2", "", "Stance lanes"), chip);
  if (report.first_stances_differ) head.append(tip(el("span", "badge", "first stances differ"),
    "Agents did not share one stance in their first message"));
  const basis = report.read_basis.includes("timing") ? ["timing only", "warn",
    "No delivered_sources on these messages: arrows assume each peer's latest earlier message was read"]
    : ["read-sets recorded", "accent", "Arrows start at the exact peer message the agent read"];
  if (report.flips.length) head.append(tip(el("span", `badge ${basis[1]}`, basis[0]), basis[2]));
  const source = report.stance_source === "regex" ? `regex ${report.pattern}` : `llm: ${report.stance_question}`;
  head.append(el("p", "muted stance-summary", `${pattern.text} Stances from ${source}${/[.?!]$/.test(source) ? "" : "."}`
    + (report.answer_key ? ` Answer key: ${report.answer_key}.` : "")));
  return head;
}

function legend(report) {
  const node = el("div", "stance-legend");
  for (const stance of report.stances) {
    const item = el("span", "stance-legend-item");
    const swatch = el("span", "stance-swatch");
    swatch.style.background = stanceColor(report, stance);
    item.append(swatch, stance);
    node.append(item);
  }
  return node;
}

function laneLabel(lane) {
  const label = el("div", "stance-label");
  label.append(el("span", "stance-agent", lane.agent_id));
  if (lane.holdout) label.append(tip(el("span", "badge accent", "holdout"),
    `Never changed stance, though ${lane.challenged_messages} of its messages followed a disagreeing peer`));
  else if (lane.flips) label.append(el("span", "muted", `${lane.flips} flip${lane.flips > 1 ? "s" : ""}`));
  return label;
}

function grid(report, actions) {
  const columns = Math.max(0, ...report.lanes.map((lane) => lane.cells.length));
  const width = columns * (CELL + COLUMN_GAP), height = report.lanes.length * (CELL + GAP);
  const flipAt = new Set(report.flips.map((flip) => flip.event_id));
  const wrap = el("div", "stance-grid");
  wrap.style.setProperty("--stance-label", `${LABEL}px`);
  const labels = el("div", "stance-labels");
  labels.append(...report.lanes.map(laneLabel));
  const canvas = el("div", "stance-canvas");
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  report.lanes.forEach((lane, row) => lane.cells.forEach((cell, column) => {
    const node = button(cell.stance ? cell.stance.slice(0, 3) : "·", () => actions.open(cell), "stance-cell");
    node.style.left = `${left(column)}px`;
    node.style.top = `${top(row)}px`;
    node.style.background = cell.stance ? stanceColor(report, cell.stance) : "";
    node.classList.toggle("stated", cell.stated);
    node.classList.toggle("flip", flipAt.has(cell.event_id));
    if (cell.correct !== null && cell.correct !== undefined) node.classList.add(cell.correct ? "correct" : "wrong");
    node.dataset.eventId = cell.event_id;
    tip(node, `${lane.agent_id} · event ${cell.seq} · ${cell.stance ?? "no stance"}`
      + (cell.stated ? "" : " (carried over)") + (cell.correct === null || cell.correct === undefined ? ""
        : cell.correct ? " · correct" : " · wrong"));
    canvas.append(node);
  }));
  const overlay = svg("svg", { class: "stance-arrows", width, height, viewBox: `0 0 ${width} ${height}` });
  const marker = svg("marker", { id: "stance-head", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7,
    markerUnits: "userSpaceOnUse", orient: "auto" });
  marker.append(svg("path", { d: "M0,0 L8,4 L0,8 z" }));
  const defs = svg("defs");
  defs.append(marker);
  overlay.append(defs);
  for (const arrow of arrows(report)) {
    overlay.append(svg("path", {
      class: "stance-arrow", d: arrowPath(arrow.from, arrow.to), "stroke-width": 1 + arrow.credit, "marker-end": "url(#stance-head)",
      "data-peer": arrow.peer, "data-flip": arrow.flip.event_id,
    }));
  }
  canvas.append(overlay);
  wrap.append(labels, canvas);
  return wrap;
}

function flipList(report, actions) {
  const list = el("div", "stance-flips");
  list.append(el("h3", "", report.flips.length ? `Flips (${report.flips.length})` : "No flips"));
  for (const flip of report.flips) {
    const row = el("div", "stance-flip");
    const toward = Object.keys(flip.toward);
    const text = el("span", "", `${flip.agent_id} · event ${flip.seq} · ${flip.from} → ${flip.to}`
      + (toward.length ? ` · toward ${toward.join(", ")}` : " · no peer it read held this stance")
      + (flip.transition ? ` · ${flip.transition}` : ""));
    const buttons = el("span", "stance-flip-actions");
    for (const peer of toward) buttons.append(button(`Read message (${peer})`, () => actions.openRead(flip, peer)));
    buttons.append(button("Open flip", () => actions.open(flip)),
      tip(button("Fork before flip", () => actions.fork(flip)),
        "Branch from the event before this message, e.g. to change what the agent reads and rerun"));
    row.append(text, buttons);
    list.append(row);
  }
  return list;
}

export function renderLanes(report, actions) {
  const node = el("div", "stance-report");
  node.append(header(report), legend(report), grid(report, actions), flipList(report, actions));
  return node;
}
