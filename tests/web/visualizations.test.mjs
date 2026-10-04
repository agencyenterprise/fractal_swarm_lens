import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { VisualizationHost, registerVisualization, unregisterVisualization, playbackInterval } from '../../src/swarm_lens/web/visualizations.js';
import { influence, linkMode, messageFlow, flowEdges } from '../../src/swarm_lens/web/graph.js';

const dom = new JSDOM('<!doctype html><section id="viz"></section>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event', 'localStorage'])
  globalThis[key] = key === 'window' ? dom.window : dom.window[key];

function recordingView(id, log) {
  return { id, title: id, mount() {
    log.push(`${id}:mount`);
    return { update: (context) => log.push(`${id}:update:${context.cursor}`), destroy: () => log.push(`${id}:destroy`) };
  } };
}

const context = (cursor) => ({ events: [], branch: { head: 9 }, state: {}, cursor, selectedId: null, agents: {} });
const tab = (root, title) => [...root.querySelectorAll('.viz-switch button')].find((node) => node.textContent === title);

test('only the visible view is mounted and updated, and the choice persists', () => {
  localStorage.clear();
  const log = [];
  registerVisualization(recordingView('first', log));
  registerVisualization(recordingView('second', log));
  const root = document.querySelector('#viz');
  const host = new VisualizationHost(root, { seek() {}, togglePlayback() {} });
  host.update(context(3));
  tab(root, 'second').click();
  host.update(context(4));
  assert.deepEqual(log, ['first:mount', 'first:update:3', 'first:destroy', 'second:mount', 'second:update:3', 'second:update:4']);
  assert.equal(tab(root, 'second').getAttribute('aria-pressed'), 'true');

  log.length = 0;
  const reopened = new VisualizationHost(document.createElement('section'), { seek() {}, togglePlayback() {} });
  reopened.update(context(5));
  assert.deepEqual(log, ['second:mount', 'second:update:5']);
  assert.throws(() => registerVisualization(recordingView('first', log)), /already registered/);
});

test('the playback speed choice sets the replay interval', () => {
  localStorage.clear();
  assert.equal(playbackInterval(), 500);
  const root = document.createElement('section');
  new VisualizationHost(root, { seek() {}, togglePlayback() {} });
  const speed = root.querySelector('.viz-speed');
  speed.value = '10';
  speed.dispatchEvent(new window.Event('change'));
  assert.equal(playbackInterval(), 100);
});

const message = (position, agent, entity, extra = {}) => ({ id: `e${position}`, position, kind: 'message.created',
  agent_id: agent, entity_id: entity, channel_id: 'debate', ...extra });

test('read edges come from delivered sources, counted per author and reader up to the cursor', () => {
  const events = [
    message(1, 'a', 'm1', { delivered_sources: [] }),
    message(2, 'b', 'm2', { delivered_sources: ['m1'] }),
    message(3, 'c', 'm3', { delivered_sources: ['m1', 'm2'] }),
    message(4, 'a', 'm4', { delivered_sources: ['m2', 'm3', 'm1'] }),
    message(5, 'b', 'm5', { delivered_sources: ['m1', 'm4'] }),
  ];
  assert.equal(linkMode(events), 'reads');
  const { edges, agents } = influence(events, 4, 'reads');
  const weights = Object.fromEntries(edges.map((edge) => [edge.key, edge.weight]));
  assert.deepEqual(weights, { 'a>b': 1, 'a>c': 1, 'b>c': 1, 'b>a': 1, 'c>a': 1 }, 'self-reads and reads after the cursor are left out');
  assert.equal(agents.get('a').reach, 2);
  assert.equal(agents.get('a').received, 2);
  assert.equal(agents.get('a').sent, 2);
  assert.deepEqual([...agents.get('c').readFrom], [['a', 1], ['b', 1]]);

  const flow = messageFlow(events[1], events, 'reads');
  assert.deepEqual(flow.incoming.map((event) => event.id), ['e1']);
  assert.deepEqual(flow.outgoing.map((event) => event.id), ['e3', 'e4']);
  assert.deepEqual(flowEdges(events[1], flow, 3).map(({ key, direction, future }) => [key, direction, future]),
    [['a>b', 'in', false], ['b>c', 'out', false], ['b>a', 'out', true]]);
});

test('without delivered sources, replies link messages; without either, channels do', () => {
  const replies = [message(1, 'a', 'm1'), message(2, 'b', 'm2', { reply_to_id: 'm1' }), message(3, 'c', 'm3', { reply_to_id: 'm1' })];
  assert.equal(linkMode(replies), 'replies');
  assert.deepEqual(influence(replies, 3, 'replies').edges.map((edge) => edge.key), ['a>b', 'a>c']);
  assert.deepEqual(messageFlow(replies[0], replies, 'replies').outgoing.map((event) => event.id), ['e2', 'e3']);
  assert.equal(linkMode([message(1, 'a', 'm1')]), 'channels');
});

test('unregistering the visible view destroys it and shows another view', () => {
  localStorage.clear();
  const log = [];
  registerVisualization(recordingView('base', log));
  const root = document.createElement('section');
  const host = new VisualizationHost(root, { seek() {}, togglePlayback() {} });
  registerVisualization(recordingView('plugin', log));
  tab(root, 'plugin').click();
  host.update(context(2));
  log.length = 0;
  unregisterVisualization('plugin');
  assert.equal(tab(root, 'plugin'), undefined);
  assert.deepEqual(log, ['plugin:destroy']);
  const pressed = [...root.querySelectorAll('.viz-switch [aria-pressed=true]')];
  assert.equal(pressed.length, 1);
  assert.equal(root.querySelectorAll('.viz-view:not([hidden])').length, 1);
  registerVisualization(recordingView('plugin', log));
});
