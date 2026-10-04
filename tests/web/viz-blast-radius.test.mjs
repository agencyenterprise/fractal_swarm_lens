import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { deliveryGraph, cascade, visibleCount, widestReach, blastRadius } from '../../src/swarm_lens/web/viz/blast-radius.js';

const message = (position, agent, entity, extra = {}) => ({ id: `e${position}`, position, kind: 'message.created',
  at: new Date(Date.UTC(2026, 9, 3, 8, 0, position)).toISOString(),
  agent_id: agent, entity_id: entity, stage_label: null, preview: `Message ${entity}`, ...extra });
const read = (position, agent, entity, sources) => message(position, agent, entity, { delivered_sources: sources });

// m1 -> m2 -> m3 -> m5, m1 -> m4; m6 reads nothing.
const delivered = [
  read(1, 'a', 'm1', []),
  { id: 'e2', position: 2, kind: 'memory.written', agent_id: 'a' },
  read(3, 'b', 'm2', ['m1']),
  read(4, 'c', 'm3', ['m2']),
  read(5, 'b', 'm4', ['m1', 'm1']),
  read(6, 'a', 'm5', ['m3', 'm4']),
  read(7, 'c', 'm6', []),
];

test('the cascade follows reads over several hops with shortest depths', () => {
  const graph = deliveryGraph(delivered);
  assert.equal(graph.mode, 'delivered');
  const result = cascade(graph, 0, graph.messages.length);
  assert.deepEqual(result.reached.map((index) => graph.messages[index].entity_id), ['m2', 'm3', 'm4', 'm5']);
  assert.deepEqual([...result.depth].map(([index, depth]) => [graph.messages[index].entity_id, depth]),
    [['m1', 0], ['m2', 1], ['m4', 1], ['m3', 2], ['m5', 2]]);
  assert.equal(result.maxDepth, 2);
  assert.equal(result.later, 5);
  assert.equal(result.laterAgents, 3);
  assert.deepEqual([...result.firstReach].map(([agent, index]) => [agent, graph.messages[index].entity_id]),
    [['b', 'm2'], ['c', 'm3'], ['a', 'm5']]);
});

test('messages after the cursor are not reached and not counted', () => {
  const graph = deliveryGraph(delivered);
  const limit = visibleCount(graph, 4);
  assert.equal(limit, 3);
  const result = cascade(graph, 0, limit);
  assert.deepEqual(result.reached.map((index) => graph.messages[index].entity_id), ['m2', 'm3']);
  assert.equal(result.later, 2);
  assert.deepEqual(widestReach(graph, limit).map(({ index, reach }) => [graph.messages[index].entity_id, reach]),
    [['m1', 2], ['m2', 1]]);
  assert.deepEqual(widestReach(graph, graph.messages.length).map(({ index, reach }) => [graph.messages[index].entity_id, reach]),
    [['m1', 4], ['m2', 2], ['m3', 1], ['m4', 1]]);
});

test('reach counts match the cascade across bitset word boundaries', () => {
  const chain = Array.from({ length: 70 }, (_, index) =>
    read(index + 1, `a${index % 3}`, `c${index}`, index ? [`c${index - 1}`, ...(index > 33 ? ['c0'] : [])] : []));
  const graph = deliveryGraph(chain);
  const ranking = widestReach(graph, graph.messages.length, 70);
  for (const { index, reach } of ranking) assert.equal(reach, cascade(graph, index, graph.messages.length).reached.length);
  assert.equal(ranking[0].reach, 69);
});

test('runs without deliveries fall back to replies, and runs with neither say so', () => {
  const replies = [message(1, 'a', 'r1'), message(2, 'b', 'r2', { reply_to_id: 'r1' }), message(3, 'a', 'r3', { reply_to_id: 'r2' })];
  const graph = deliveryGraph(replies);
  assert.equal(graph.mode, 'reply');
  assert.equal(cascade(graph, 0, 3).maxDepth, 2);

  const dom = new JSDOM('<div id="root"></div>');
  globalThis.document = dom.window.document;
  const root = dom.window.document.getElementById('root');
  const view = blastRadius.mount(root, {});
  view.update({ events: [message(1, 'a', 'x'), message(2, 'b', 'y')], cursor: 2, selectedId: null, agents: {} });
  assert.equal(root.textContent, 'This run records no delivery data.');
  view.destroy();
  assert.equal(root.childElementCount, 0);
});

test('the ranking and the tree select the message that was clicked', () => {
  const dom = new JSDOM('<div id="root"></div>');
  globalThis.document = dom.window.document;
  const root = dom.window.document.getElementById('root');
  const selected = [];
  const view = blastRadius.mount(root, { select: (event) => selected.push(event.id) });
  const agents = { a: { id: 'a', name: 'Ada' }, b: { id: 'b', name: 'Bo' }, c: { id: 'c', name: 'Cy' } };
  view.update({ events: delivered, cursor: 7, selectedId: null, agents });
  const rows = root.querySelectorAll('.br-rank');
  assert.equal(rows.length, 4);
  rows[0].click();
  assert.deepEqual(selected, ['e1']);

  view.update({ events: delivered, cursor: 1, selectedId: 'e1', agents });
  assert.equal(root.querySelectorAll('.br-node').length, 5);
  assert.match(root.querySelector('.br-stats').textContent, /4 of 5 later messages \(80%\)/);
  root.querySelector('.br-node:not(.br-node-root)').click();
  assert.deepEqual(selected, ['e1', 'e3']);
});
