// Display coordinates only. Saved timestamps and event positions never change.
const GAP_THRESHOLD = 60_000;
const FOLDED_GAP = 5_000;

export function gapDuration(milliseconds) {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60), remainder = minutes % 60;
  return `${hours}h${remainder ? ` ${remainder}m` : ''}`;
}

export function timelineTime(events, compact = true) {
  const ordered = [...events].sort((a, b) => a.position - b.position);
  const positions = new Map(), gaps = [], segments = [];
  let previous, elapsed = 0, segment;
  for (const event of ordered) {
    const parsed = Date.parse(event.at);
    // Concurrent captures can arrive with older timestamps. Keep cursor order stable.
    const actual = Math.max(previous ?? 0, Number.isFinite(parsed) ? parsed : (previous ?? 0));
    if (previous === undefined) {
      segment = { start: 0, end: 0, actualStart: actual, actualEnd: actual };
      segments.push(segment);
    } else {
      const delta = actual - previous;
      if (compact && delta > GAP_THRESHOLD) {
        gaps.push({ start: elapsed, end: elapsed + FOLDED_GAP, from: previous, to: actual,
                    duration: delta, before: event.position - 1, after: event.position });
        elapsed += FOLDED_GAP;
        segment = { start: elapsed, end: elapsed, actualStart: actual, actualEnd: actual };
        segments.push(segment);
      } else elapsed += delta;
      segment.end = elapsed;
      segment.actualEnd = actual;
    }
    positions.set(event.id, elapsed);
    previous = actual;
  }
  return {
    ordered, gaps, segments,
    at: event => positions.get(event.id) ?? 0,
    ticks(step) {
      return segments.flatMap(segment => {
        const ticks = [];
        for (let actual = Math.ceil(segment.actualStart / step) * step; actual <= segment.actualEnd; actual += step)
          ticks.push({ at: segment.start + actual - segment.actualStart, actual });
        return ticks;
      });
    },
  };
}
