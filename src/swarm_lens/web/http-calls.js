// HTTP calls per website: one Plugins menu action that opens a modal chart of calls per website over the session.
import { $, el, button, svg, post, failure, formatNumber, time, toast } from "./ui.js";
import { openDialog } from "./dialog.js";

const SERIES_LIMIT = 5;
const CHART = { width: 760, height: 220, left: 34, right: 12, top: 18, bottom: 26 };
const X_TICKS = 5;

const plural = (count, noun) => `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;

function rangeLabel(output, first, last = first) {
  const [start, end] = [output.bins[first].start, output.bins[last].end];
  return output.axis === "time" ? `${time(start)}–${time(end)}` : `events ${start}–${end}`;
}

// Spiking websites always get their own line; the remaining slots go to the busiest websites.
function chartSeries(output) {
  const spiking = new Set(output.spikes.map((spike) => spike.domain));
  const chosen = new Set([...spiking].slice(0, SERIES_LIMIT));
  for (const domain of output.domains) if (chosen.size < SERIES_LIMIT) chosen.add(domain.domain);
  const shown = output.domains.filter((domain) => chosen.has(domain.domain));
  const series = shown.map((domain, index) => ({ ...domain, label: domain.domain, tone: `s${index + 1}` }));
  const rest = output.domains.filter((domain) => !chosen.has(domain.domain));
  if (rest.length) {
    const counts = output.bins.map((_, bin) => rest.reduce((sum, domain) => sum + domain.counts[bin], 0));
    series.push({ label: `other (${plural(rest.length, "website")})`, tone: "other", counts, spike_bins: [] });
  }
  return series;
}

function niceMax(value) {
  const step = 10 ** Math.floor(Math.log10(Math.max(value, 1)));
  return [1, 2, 2.5, 5, 10].map((factor) => factor * step).find((candidate) => candidate >= value);
}

function chart(output, series, onJump) {
  const { width, height, left, right, top, bottom } = CHART;
  const bins = output.bins.length;
  const yMax = niceMax(Math.max(...series.flatMap((item) => item.counts)));
  const x = (bin) => left + ((bin + 0.5) / bins) * (width - left - right);
  const y = (count) => top + (1 - count / yMax) * (height - top - bottom);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "http-chart", role: "img",
    "aria-label": `HTTP calls per website across ${plural(bins, "bin")}; the table below lists the same data.` });

  for (const value of [0, yMax / 2, yMax]) {
    root.append(svg("line", { class: "http-grid", x1: left, x2: width - right, y1: y(value), y2: y(value) }),
      svg("text", { class: "http-axis", x: left - 6, y: y(value) + 4, "text-anchor": "end" }, formatNumber(value)));
  }
  const tickEvery = Math.max(1, Math.round(bins / X_TICKS));
  for (let bin = 0; bin < bins; bin += tickEvery) {
    const { start } = output.bins[bin];
    root.append(svg("text", { class: "http-axis", x: x(bin), y: height - 8, "text-anchor": "middle" },
      output.axis === "time" ? time(start).slice(0, 5) : `#${start}`));
  }

  const crosshair = svg("line", { class: "http-crosshair", y1: top, y2: height - bottom, visibility: "hidden" });
  root.append(crosshair);
  for (const item of [...series].reverse()) {
    root.append(svg("polyline", { class: `http-line ${item.tone}`, points: item.counts.map((count, bin) => `${x(bin)},${y(count)}`).join(" ") }));
  }

  const tooltip = el("div", "http-tooltip");
  tooltip.hidden = true;
  const hoverWidth = (width - left - right) / bins;
  const showBin = (bin) => {
    crosshair.setAttribute("x1", x(bin));
    crosshair.setAttribute("x2", x(bin));
    crosshair.setAttribute("visibility", "visible");
    tooltip.replaceChildren(el("strong", "", rangeLabel(output, bin)), ...series.map((item) => {
      const row = el("span", `http-tip-row ${item.tone}`);
      row.append(el("i", "http-swatch"), el("span", "", item.label), el("span", "mono", formatNumber(item.counts[bin])));
      return row;
    }));
    tooltip.style.left = `${(x(bin) / width) * 100}%`;
    tooltip.classList.toggle("flip", bin > bins / 2);
    tooltip.hidden = false;
  };
  const hideBin = () => { crosshair.setAttribute("visibility", "hidden"); tooltip.hidden = true; };
  for (let bin = 0; bin < bins; bin++) {
    const target = svg("rect", { class: "http-hover", x: x(bin) - hoverWidth / 2, y: top, width: hoverWidth, height: height - top - bottom });
    target.addEventListener("pointerenter", () => showBin(bin));
    root.append(target);
  }
  root.addEventListener("pointerleave", hideBin);

  for (const item of series) {
    for (const bin of item.spike_bins) {
      const spike = output.spikes.find((candidate) => candidate.domain === item.domain
        && candidate.first_bin <= bin && bin <= candidate.last_bin);
      const count = item.counts[bin];
      const marker = svg("g", { class: `http-spike ${item.tone}`, role: "button", tabindex: "0",
        "aria-label": `Spike: ${item.domain}, ${plural(count, "call")} in ${rangeLabel(output, bin)}. Open the first call.` });
      marker.append(svg("circle", { cx: x(bin), cy: y(count), r: 6 }),
        svg("text", { x: x(bin), y: y(count) - 10, "text-anchor": "middle" }, formatNumber(count)));
      marker.addEventListener("click", () => onJump(spike.events[0]));
      marker.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onJump(spike.events[0]); }
      });
      root.append(marker);
    }
  }

  const frame = el("div", "http-chart-frame");
  frame.append(root, tooltip);
  return frame;
}

function legend(series) {
  const list = el("ul", "http-legend");
  list.setAttribute("aria-label", "Websites");
  list.append(...series.map((item) => {
    const entry = el("li", item.tone);
    entry.append(el("i", "http-swatch"), el("span", "", item.label));
    return entry;
  }));
  return list;
}

function spikeList(output, onJump) {
  const list = el("div", "http-spikes");
  list.append(el("h3", "section-title", plural(output.spikes.length, "spike")),
    ...output.spikes.map((spike) => {
      const item = button("", () => onJump(spike.events[0]), "http-spike-chip");
      item.append(el("strong", "", spike.domain), el("span", "", `${plural(spike.count, "call")} · ${rangeLabel(output, spike.first_bin, spike.last_bin)}`),
        el("span", "muted", `usual ≤ ${formatNumber(Math.ceil(spike.threshold) - 1)}`));
      return item;
    }));
  return list;
}

function websiteTable(output, series, onJump) {
  const tones = new Map(series.map((item) => [item.domain, item.tone]));
  const table = el("table", "http-table");
  const head = el("tr");
  head.append(...["Website", "Calls", "Peak", ""].map((label) => el("th", "", label)));
  table.append(el("thead"), el("tbody"));
  table.tHead.append(head);
  table.tBodies[0].append(...output.domains.map((domain) => {
    const row = el("tr", tones.get(domain.domain) || "other");
    const name = el("td");
    const open = button("", () => onJump(domain.first_seen), "http-site");
    open.title = `Open the first call to ${domain.domain}`;
    open.append(el("i", "http-swatch"), el("span", "", domain.domain));
    name.append(open);
    const spike = el("td");
    const spikes = output.spikes.filter((item) => item.domain === domain.domain).length;
    if (spikes) spike.append(el("span", "badge warn", spikes === 1 ? "Spike" : `${spikes} spikes`));
    row.append(name, el("td", "mono", formatNumber(domain.total)),
      el("td", "mono", `${formatNumber(domain.peak_count)} · ${rangeLabel(output, domain.peak_bin)}`), spike);
    return row;
  }));
  const frame = el("div", "http-table-frame");
  frame.append(table);
  return frame;
}

function report(record, onJump) {
  const output = record.output;
  const root = el("div", "http-report");
  if (!output.total_calls) {
    root.append(el("p", "http-empty", "No HTTP calls recorded in this run"),
      el("p", "muted", `Scanned events 1–${formatNumber(record.cursor)}.`));
    return root;
  }
  const series = chartSeries(output);
  root.append(el("p", "muted", `${plural(output.total_calls, "call")} to ${plural(output.domains.length, "website")} · events 1–${formatNumber(record.cursor)} · binned by ${output.axis_reason}`),
    legend(series), chart(output, series, onJump));
  if (output.spikes.length) root.append(spikeList(output, onJump));
  root.append(websiteTable(output, series, onJump),
    el("p", "muted http-note", `${output.spike_rule} ${output.interpretation}`));
  return root;
}

async function openHttpCalls(manifest, host) {
  const selection = host.selection();
  if (!selection) return toast("Select a run to analyze");
  if (selection.cursor < 1) return toast("Select at least one event first");
  const root = el("div", "http-calls");
  const status = el("p", "http-progress with-spinner", "Counting HTTP calls…");
  status.setAttribute("role", "status");
  root.append(status);
  openDialog("HTTP calls per website", root, { kicker: selection.name });
  try {
    const record = await post(`${manifest.api_prefix.replace(/^\/api/, "")}/analyses`,
      { branch_id: selection.branchId, cursor: selection.cursor });
    if (!root.isConnected) return;
    root.replaceChildren(report(record, (place) => {
      $("#dialog").close();
      host.showEvidenceEvent(record.branch_id, record.cursor, place.event_id).catch(failure);
    }));
  } catch (error) {
    if (root.isConnected) root.replaceChildren(el("p", "http-error", error.message));
  }
}

export function installHttpCalls(manifest, host) {
  host.addAction({ id: "http-calls", label: "HTTP calls per website…", onClick: () => openHttpCalls(manifest, host) });
}
