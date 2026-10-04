import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'));
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event'])
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
globalThis.ResizeObserver = class { observe() {} };
globalThis.requestAnimationFrame = callback => { callback(); return 1; };
const { EventTimeline, sparkline } = await import('../../src/swarm_lens/web/timeline.js');
const { findingsForEvent } = await import('../../src/swarm_lens/web/findings.js');
const { renderInspector } = await import('../../src/swarm_lens/web/inspect.js');

const base = Date.parse('2026-10-03T08:45:00Z');
const message = (position, agent, seconds) => ({ id: `e${position}`, position, kind: 'message.created', agent_id: agent,
  channel_id: 'chat', label: 'Message', preview: `Message ${position}`, at: new Date(base + seconds * 1000).toISOString() });
const events = [
  { ...message(1, 'a', 0), kind: 'agent.added', agent_name: 'Alpha' },
  { ...message(2, 'b', 0), kind: 'agent.added', agent_name: 'Beta' },
  message(3, 'a', 10), message(4, 'b', 20), message(5, 'a', 30), message(6, 'b', 40),
];
const findings = {
  series: [
    { plugin: 'activity', name: 'messages', agent_id: null, points: [[3, 1], [4, 2], [5, 3], [6, 4]], downsampled: false },
    { plugin: 'activity', name: 'length', agent_id: 'b', points: [[4, 10], [6, 30]], downsampled: false },
    { plugin: 'activity', name: 'length', agent_id: 'a', points: [[3, 5], [5, 5]], downsampled: false },
  ],
  annotations: [
    { plugin: 'echo', seq_from: 4, seq_to: 6, label: 'Echoes Alpha', agent_id: 'b', score: 0.75, data: {}, cited_event_ids: [] },
    { plugin: 'drift', seq_from: 3, seq_to: 3, label: 'Topic drift', agent_id: null, score: null, data: {}, cited_event_ids: ['e6'] },
  ],
};

function setup() {
  const root = document.querySelector('#timeline');
  const calls = { select: [], seek: [] };
  const timeline = new EventTimeline(root, { onSeek: p => calls.seek.push(p), onSelect: e => calls.select.push(e.id),
    onContext() {}, onAgent() {}, onPlay() {} });
  const viewport = root.querySelector('.tl-viewport');
  Object.defineProperty(viewport, 'clientWidth', { value: 1100 });
  viewport.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1100 });
  timeline.setData(events, { id: 'main', parent_id: null, head: 6 });
  timeline.setFindings(findings);
  timeline.setCursor(6);
  return { root, timeline, calls };
}

test('sparkline scales values into the row and places each point at its x', () => {
  assert.deepEqual(sparkline([[1, 0], [2, 5], [3, 10]], seq => seq * 100, 30, 5), [[100, 25], [200, 15], [300, 5]]);
  assert.deepEqual(sparkline([[1, 7], [2, 7]], seq => seq, 30, 5), [[1, 15], [2, 15]]);
});

test('metric tracks sit under the lanes, one per series, aligned with the event x mapping', () => {
  const { root, timeline } = setup();
  const rows = [...root.querySelectorAll('.tl-track')];
  assert.deepEqual(rows.map(row => row.querySelector('.tl-lane-name').textContent),
    ['activity · length (Alpha)', 'activity · length (Beta)', 'activity · messages']);
  assert.equal(root.querySelector('.tl-lanes').nextElementSibling, root.querySelector('.tl-tracks'));
  const xs = rows[1].querySelector('polyline').getAttribute('points').split(' ').map(pair => Number(pair.split(',')[0]));
  assert.deepEqual(xs, [timeline.x(events[3]), timeline.x(events[5])]);
  assert.equal(rows[1].style.getPropertyValue('--c'), '#5b84c4', 'an agent track uses its lane color');
  assert.equal(timeline.height, timeline.rulerHeight + 2 * 34 + 3 * 30);

  rows[2].querySelector('svg').dispatchEvent(new window.MouseEvent('pointermove', { bubbles: true, clientX: timeline.x(events[4]) + 1 }));
  assert.match(root.querySelector('.tl-tooltip').textContent, /activity · messages.*Event 5 · 3/);

  const toggle = [...root.querySelectorAll('.tl-check')].find(row => row.textContent === 'Metric tracks');
  assert.equal(toggle.hidden, false);
  toggle.firstChild.checked = false;
  toggle.firstChild.dispatchEvent(new window.Event('change'));
  assert.equal(root.querySelectorAll('.tl-track').length, 0);
});

test('annotations span their events in the agent lane or on the ruler; hover explains and click selects the start', () => {
  const { root, timeline, calls } = setup();
  const [inLane] = root.querySelectorAll('.tl-annotations .tl-annotation');
  const [onRuler] = root.querySelectorAll('.tl-ruler .tl-annotation');
  assert.equal(inLane.style.left, `${timeline.x(events[3]) - 3}px`);
  assert.equal(inLane.style.width, `${timeline.x(events[5]) - timeline.x(events[3]) + 6}px`);
  assert.equal(inLane.style.top, `${timeline.laneY('b') + 34 / 2 - 8}px`);
  assert.equal(onRuler.style.left, `${timeline.x(events[2]) - 3}px`);

  inLane.dispatchEvent(new window.MouseEvent('pointerover', { bubbles: true }));
  assert.equal(root.querySelector('.tl-tooltip').textContent, 'Echoes AlphaBetaecho · score 0.75Events 4–6');
  onRuler.dispatchEvent(new window.MouseEvent('pointerover', { bubbles: true }));
  assert.equal(root.querySelector('.tl-tooltip').textContent, 'Topic driftdriftEvent 3');

  inLane.click();
  onRuler.click();
  assert.deepEqual(calls.select, ['e4', 'e3']);
  assert.deepEqual(calls.seek, [], 'an annotation click does not also seek like an empty lane');
});

test('the inspector lists findings that contain or cite the selected event, and hides the section otherwise', () => {
  assert.deepEqual(findingsForEvent(findings, events[5]).annotations.map(a => a.label), ['Echoes Alpha', 'Topic drift']);
  assert.deepEqual(findingsForEvent(findings, events[2]).metrics,
    [{ plugin: 'activity', name: 'messages', agent_id: null, value: 1 }, { plugin: 'activity', name: 'length', agent_id: 'a', value: 5 }]);

  const root = document.createElement('aside');
  const state = { agents: { a: { id: 'a', name: 'Alpha' }, b: { id: 'b', name: 'Beta' } }, environment: {} };
  const render = (event, found) => renderInspector(root, { type: 'event', event, detail: { data: {} } },
    { state, events, comments: { eventSection: () => null }, findings: found, interventions: [], actions: {} });
  render(events[3], findings);
  const section = [...root.querySelectorAll('.ins-section')].find(node => node.firstChild.textContent === 'Findings');
  assert.deepEqual([...section.querySelectorAll('.ins-finding-label')].map(node => node.textContent), ['Echoes Alpha']);
  assert.equal(section.querySelector('.ins-finding-source').textContent, 'echo · events 4–6 · score 0.75 · Beta');
  assert.deepEqual([...section.querySelectorAll('.ins-metrics dt, .ins-metrics dd')].map(node => node.textContent),
    ['activity · messages', '2', 'activity · length (Beta)', '10']);
  render(events[0], findings);
  assert.ok(![...root.querySelectorAll('.section-title')].some(node => node.textContent === 'Findings'));
});
