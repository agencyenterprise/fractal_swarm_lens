import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { timelineTime, gapDuration } from '../../src/swarm_lens/web/timeline-time.js';
import { EventTimeline } from '../../src/swarm_lens/web/timeline.js';
import { WorkspaceViews } from '../../src/swarm_lens/web/workspace.js';

const base = Date.parse('2026-10-03T08:45:00Z');
const event = (position, seconds, extra = {}) => ({ id: `e${position}`, position,
  at: new Date(base + seconds * 1000).toISOString(), kind: 'message.created',
  agent_id: 'a', channel_id: 'chat', label: 'Message', preview: `Response ${position}`, ...extra });
const history = [event(1, 0, {kind: 'agent.added', agent_name: 'Debater'}),
  event(2, 0, {kind: 'channel.created', agent_id: null, entity_id: 'chat', channel_name: 'Debate', channel_id: null}),
  event(3, 0), event(4, 15), event(5, 30),
  event(6, 30, {kind: 'environment.updated', agent_id: null, intervention: true}),
  event(7, 15*3600), event(8, 15*3600 + 15), event(9, 15*3600 + 30)];
const branch = { id: 'child', parent_id: 'parent', fork_position: 5, head: 9 };

test('hours between a fork and continuation collapse without changing recorded timestamps', () => {
  const before = structuredClone(history);
  const clock = timelineTime(history);
  assert.equal(clock.at(history[6]) - clock.at(history[5]), 5000);
  assert.equal(clock.at(history[7]) - clock.at(history[6]), 15000);
  assert.equal(clock.gaps.length, 1);
  assert.equal(clock.gaps[0].duration, (15*3600 - 30)*1000);
  assert.deepEqual(history, before);
  const ticks = clock.ticks(15000);
  assert.ok(ticks.some(tick => tick.actual === base+15*3600*1000 && tick.at === 35000));
  assert.ok(!ticks.some(tick => tick.actual > base+30000 && tick.actual < base+15*3600*1000));
  const actual = timelineTime(history, false);
  assert.equal(actual.at(history[6]) - actual.at(history[5]), (15*3600 - 30)*1000);
  assert.equal(actual.gaps.length, 0);
});

test('nested continuations and appended live events keep existing coordinates stable', () => {
  const clock = timelineTime(history);
  const extended = [...history, event(10, 2*86400), event(11, 2*86400+10)];
  const next = timelineTime(extended);
  for (const row of history) assert.equal(next.at(row), clock.at(row));
  assert.equal(next.gaps.length, 2);
  assert.equal(next.at(extended[9]), next.at(history[8])+5000);
  assert.equal(gapDuration(90*60000), '1h 30m');
});

test('out-of-order timestamps do not reorder timeline event cursors', () => {
  const rows = [event(1, 10), event(2, 5), event(3, 20)];
  const clock = timelineTime(rows);
  assert.deepEqual(clock.ordered.map(e => e.position), [1, 2, 3]);
  assert.deepEqual(rows.map(e => clock.at(e)), [0, 0, 10000]);
  assert.equal(timelineTime([]).gaps.length, 0);
});

const injection = event(10, 15*3600 + 31, {kind: 'observation.recorded', label: 'instruction_injection',
  intervention: true, stage_label: 'Instruction injection installed'});

function setup(events = history) {
  const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'));
  for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event'])
    globalThis[key] = key === 'window' ? dom.window : dom.window[key];
  dom.window.HTMLElement.prototype.scrollTo = function (left, top) {
    this.scrollLeft = left;
    this.scrollTop = top;
  };
  globalThis.ResizeObserver = class { observe() {} };
  globalThis.requestAnimationFrame = callback => { callback(); return 1; };
  const calls = { seek: [], select: [], context: [] };
  const root = document.querySelector('#timeline');
  const timeline = new EventTimeline(root, {
    onSeek: p => calls.seek.push(p), onSelect: e => calls.select.push(e.id),
    onContext: (e, p) => calls.context.push([e?.id ?? null, p]), onAgent() {}, onPlay() {} });
  const viewport = root.querySelector('.tl-viewport');
  Object.defineProperty(viewport, 'clientWidth', { value: 1100 });
  viewport.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1100 });
  timeline.setData(events, branch);
  timeline.setCursor(9, true);
  return { root, timeline, viewport, calls };
}

const marker = (root, position) => root.querySelector(`.tl-marker[data-position="${position}"]`);

test('a short fork shows parent and continuation together, with the fork line and collapsed gap', () => {
  const { root, timeline, viewport } = setup();
  assert.equal(viewport.scrollLeft, 0);
  assert.ok(marker(root, 5) && marker(root, 7));
  assert.ok(timeline.x(history[6]) - timeline.x(history[4]) < 80);
  assert.match(root.querySelector('.tl-gap-label').title, /08:45:30.*23:45:00/s);
  assert.equal(root.querySelector('.tl-fork-line').style.left, `${timeline.x(history[4])}px`);
  assert.ok(root.querySelector('.tl-fork-flag'));
  assert.equal(root.querySelector('.tl-playhead').style.left, `${timeline.x(history[8])}px`);
  assert.equal(root.querySelector('.tl-position').textContent, '9 / 9');
});

test('clicks, steps, and keys seek to the nearest shown event', () => {
  const { root, timeline, calls } = setup();
  const lane = root.querySelector('.tl-lane:not(.is-channel)');
  lane.dispatchEvent(new window.MouseEvent('click', { bubbles: true, clientX: timeline.x(history[6]) + 1 }));
  lane.dispatchEvent(new window.MouseEvent('click', { bubbles: true, clientX: 20 }));
  timeline.setCursor(5);
  timeline.step(1);
  timeline.step(-1);
  root.querySelector('.tl-viewport').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  assert.deepEqual(calls.seek, [7, 6, 4, 9]);
  marker(root, 4).dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  marker(root, 4).click();
  assert.deepEqual(calls.context, [['e4', 4]]);
  assert.deepEqual(calls.select, ['e4']);
  assert.ok(marker(root, 4).classList.contains('is-selected'));
  assert.ok(marker(root, 7).classList.contains('is-future'));
});

test('the injection event is a flagged risk marker', () => {
  const { root } = setup([...history, injection]);
  const node = marker(root, 10);
  assert.equal(node.dataset.tone, 'risk');
  assert.equal(node.querySelector('.tl-flag').textContent, 'Instruction injection installed');
});

test('only markers inside the horizontal viewport are rendered, and nodes are reused', () => {
  const many = [history[0], history[1],
    ...Array.from({ length: 3000 }, (_, index) => event(index + 3, index * 2))];
  const { root, timeline, viewport } = setup(many);
  timeline.fit();
  timeline.zoomBy(400);
  const rendered = root.querySelectorAll('.tl-marker');
  assert.ok(rendered.length > 0 && rendered.length < 200, `${rendered.length} markers in the DOM`);
  const before = new Set(rendered);
  viewport.scrollLeft += 600;
  viewport.dispatchEvent(new window.Event('scroll'));
  const after = [...root.querySelectorAll('.tl-marker')];
  assert.ok(after.length < 200);
  assert.ok(after.filter(node => !before.has(node)).length <= 2, 'scrolled-in markers reuse pooled nodes');
  assert.notEqual(after[0].dataset.position, rendered[0].dataset.position);
});

test('turning gap compression off and on preserves the cursor and original clock span', () => {
  const { root, timeline } = setup();
  const compact = [...root.querySelectorAll('.tl-check')].find(row => row.textContent === 'Compact gaps').firstChild;
  compact.checked = false;
  compact.dispatchEvent(new window.Event('change'));
  assert.equal(timeline.cursor, 9);
  assert.equal(root.querySelector('.tl-gap-label'), null);
  assert.equal(timeline.end - timeline.start, (15*3600 + 30)*1000);
  compact.checked = true;
  compact.dispatchEvent(new window.Event('change'));
  assert.equal(timeline.end - timeline.start, 65000);
  assert.equal(timeline.cursor, 9);
  timeline.fit();
  assert.ok(marker(root, 3) && marker(root, 9));
});

test('changing branches discards old horizontal scroll without breaking report view restoration', () => {
  const { viewport } = setup();
  const frames = [];
  globalThis.requestAnimationFrame = callback => frames.push(callback);
  const views = new WorkspaceViews(() => {});
  views.register({id: 'timeline', title: 'Timeline', panel: document.querySelector('#workspace-timeline')});
  views.register({id: 'report', title: 'Report'});
  views.show('timeline');
  frames.shift()();
  viewport.scrollLeft = 400;
  views.show('report');
  frames.shift()();
  viewport.scrollLeft = 0;
  views.show('timeline');
  frames.shift()();
  assert.equal(viewport.scrollLeft, 400, 'Returning from a report keeps timeline scroll');
  views.show('timeline'); // An old restoration may still be waiting for its animation frame.
  views.reset();
  views.show('timeline');
  viewport.scrollLeft = 25; // The new branch has already positioned its cursor.
  frames.forEach(frame => frame());
  assert.equal(viewport.scrollLeft, 25, 'The previous branch must not overwrite the new cursor position');
});
