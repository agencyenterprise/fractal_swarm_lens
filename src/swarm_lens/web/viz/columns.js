// The x axis every visualization shares: stages when the run's stage labels describe phases, otherwise
// equal time buckets. Stage labels that only mark a few events ("Task injection", "World event") are
// kept as markers on the time bucket they fall in.
import { stageOf, time } from "../ui.js";

export const MAX_COLUMNS = 60;
const STAGE_SHARE = 0.8;
const MIN_STAGES = 3;
const MIN_BUCKETS = 12;
const MAX_BUCKETS = 24;

// `counts` picks the events the view places (messages, tool calls, ...). Returns columns in display order,
// each { label, short, start, end, markers } with start/end the first and last position it holds, and
// `columnOf`: event id -> column index for every counted event.
export function columnScheme(events, counts = () => true) {
  const counted = events.filter(counts);
  if (!counted.length) return { unit: "stages", columns: [], columnOf: new Map() };
  return describesPhases(counted) ? stageScheme(counted) : timeScheme(counted, events);
}

// Tooltip text for a column header: its full label plus any stage markers inside it.
export function columnTitle(column) {
  return column.markers.length ? `${column.label} · ${column.markers.join(", ")}` : column.label;
}

function describesPhases(counted) {
  const labels = counted.map(stageOf).filter(Boolean);
  return labels.length >= STAGE_SHARE * counted.length && new Set(labels).size >= MIN_STAGES;
}

// Stages in order of first appearance; an unlabeled event joins the nearest preceding stage
// (the first stage when no label precedes it).
function stageScheme(counted) {
  const indexOf = new Map();
  const columns = [];
  const columnOf = new Map();
  let current = counted.map(stageOf).find(Boolean);
  for (const event of counted) {
    current = stageOf(event) ?? current;
    if (!indexOf.has(current)) {
      indexOf.set(current, columns.length);
      columns.push({ label: current, start: event.position, end: event.position, markers: [] });
    }
    const index = indexOf.get(current);
    columns[index].end = Math.max(columns[index].end, event.position);
    columnOf.set(event.id, index);
  }
  const shorts = shortLabels(columns.map((column) => column.label));
  columns.forEach((column, index) => (column.short = shorts[index]));
  return groupStages(columns, columnOf);
}

// Numbered stages ("Debate round 2" after "Debate round 1") keep only their number in the header.
function shortLabels(labels) {
  return labels.map((label, index) => {
    const [, prefix, number] = label.match(/^(.*\D)(\d+)$/) || [];
    const previous = labels[index - 1]?.match(/^(.*\D)(\d+)$/);
    return prefix && previous?.[1] === prefix ? number : label;
  });
}

const trailingNumber = (label) => label.match(/(\d+)$/)?.[1] ?? label;

// Very long runs group consecutive stages so the axis stays bounded.
function groupStages(columns, columnOf) {
  if (columns.length <= MAX_COLUMNS) return { unit: "stages", columns, columnOf };
  const size = Math.ceil(columns.length / MAX_COLUMNS);
  const grouped = [];
  for (let index = 0; index < columns.length; index += size) {
    const group = columns.slice(index, index + size);
    const first = group[0];
    const last = group.at(-1);
    grouped.push({
      label: group.length > 1 ? `${first.label} – ${last.label}` : first.label,
      short: group.length > 1 ? `${trailingNumber(first.label)}–${trailingNumber(last.label)}` : trailingNumber(first.label),
      start: Math.min(...group.map((column) => column.start)),
      end: Math.max(...group.map((column) => column.end)),
      markers: [],
    });
  }
  for (const [id, index] of columnOf) columnOf.set(id, Math.floor(index / size));
  return { unit: "stages", columns: grouped, columnOf };
}

function timeOf(event) {
  const at = Date.parse(event.at);
  if (Number.isNaN(at)) throw new Error(`Event ${event.id} has no valid time: ${event.at}`);
  return at;
}

// About two columns per square root of the event count, between 12 and 24, never more than events.
function bucketCount(eventCount) {
  return Math.min(eventCount, Math.max(MIN_BUCKETS, Math.min(MAX_BUCKETS, Math.round(2 * Math.sqrt(eventCount)))));
}

// Elapsed time since the first bucket, e.g. "0:14" or "1:02:05".
function elapsed(milliseconds) {
  const seconds = Math.round(milliseconds / 1000);
  const pad = (value) => String(value).padStart(2, "0");
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${pad(minutes % 60)}:${pad(seconds % 60)}`
    : `${minutes}:${pad(seconds % 60)}`;
}

function timeScheme(counted, events) {
  const times = counted.map(timeOf);
  const first = times.reduce((low, value) => Math.min(low, value));
  const last = times.reduce((high, value) => Math.max(high, value));
  const count = last > first ? bucketCount(counted.length) : 1;
  const width = (last - first) / count;
  const bucketOf = (at) => (count === 1 ? 0 : Math.max(0, Math.min(count - 1, Math.floor((at - first) / width))));
  const columns = Array.from({ length: count }, (_, index) => ({
    label: `${time(first + index * width)}–${time(first + (index + 1) * width)}`,
    short: elapsed(index * width),
    start: null, end: null, markers: [],
  }));
  const columnOf = new Map();
  counted.forEach((event, order) => {
    const index = bucketOf(times[order]);
    const column = columns[index];
    column.start = Math.min(column.start ?? Infinity, event.position);
    column.end = Math.max(column.end ?? -Infinity, event.position);
    columnOf.set(event.id, index);
  });
  // An empty bucket sits just before the next event, so it dims and outlines with its neighbours.
  for (let index = count - 1, next = Infinity; index >= 0; index--) {
    if (columns[index].start === null) columns[index].start = columns[index].end = next;
    next = columns[index].start;
  }
  addMarkers(columns, events, bucketOf);
  return { unit: "intervals", columns, columnOf };
}

function addMarkers(columns, events, bucketOf) {
  for (const event of events) {
    const stage = stageOf(event);
    if (!stage) continue;
    const { markers } = columns[bucketOf(timeOf(event))];
    if (!markers.includes(stage)) markers.push(stage);
  }
}
