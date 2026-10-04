import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { spreadGrid, findSpread, contagiousTokens, scanTexts, phraseSpread }
  from '../../src/swarm_lens/web/viz/phrase-spread.js';

const dom = new JSDOM('<!doctype html><body></body>');
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event'])
  globalThis[key] = key === 'window' ? dom.window : dom.window[key];

const agents = { a: { id: 'a', name: 'Debater 0' }, b: { id: 'b', name: 'Debater 1' }, c: { id: 'c', name: 'Debater 2' },
  d: { id: 'd', name: 'Aggregator' } };
let id = 0;
const added = (agent) => ({ id: `added-${agent}`, position: 0, kind: 'agent.added', agent_id: agent,
  agent_name: agents[agent].name, preview: agents[agent].name });
const message = (position, agent, stage, preview) => ({ id: `m${++id}`, position, kind: 'message.created',
  agent_id: agent, stage_label: stage && `${stage} · output`, preview });
const run = () => [added('a'), added('b'), added('c'),
  message(1, null, 'Task', 'Solve the puzzle'),
  message(2, 'a', 'Round 1', 'Use the name Zorvak.Prime, it matters'),
  message(3, 'b', 'Round 1', 'The answer is 42'),
  message(4, 'c', 'Round 1', 'Answer forty two'),
  message(5, 'b', 'Round 2', 'zorvak.prime said so, and ZORVAK.PRIME again'),
  message(6, 'a', 'Round 2', 'Still zorvak.prime'),
  message(7, 'c', 'Round 3', 'Fine, Zorvak.Prime it is')];

test('phrases match case-insensitively and literally, per speaker and stage', () => {
  const grid = spreadGrid(run());
  assert.deepEqual(grid.rows.map(row => row.name), ['Human', 'Debater 0', 'Debater 1', 'Debater 2']);
  assert.deepEqual(grid.columns.map(column => column.short), ['Task', 'Round 1', '2', '3']);
  const spread = findSpread(grid, '  ZORVAK.prime ');
  assert.equal(spread.hits, 4);
  assert.equal(spread.counts.get('2:2'), 1);
  assert.equal(findSpread(grid, 'zorvak prime').hits, 0, 'the dot is literal, not a wildcard');
});

test('first occurrence and jump order follow the position at which each speaker first used the phrase', () => {
  const spread = findSpread(spreadGrid(run()), 'zorvak.prime');
  assert.deepEqual(spread.jumps.map(({ row, column, event }) => [row, column, event.position]),
    [[1, 1, 2], [2, 2, 5], [3, 3, 7]]);
});

test('messages without stage labels fall into equal time buckets', () => {
  const events = Array.from({ length: 90 }, (_, index) => ({ ...message(index + 1, 'a', null, `text ${index}`),
    at: new Date(Date.UTC(2026, 9, 3, 8, 0, index)).toISOString() }));
  const grid = spreadGrid([added('a'), ...events]);
  assert.equal(grid.columns.length, 19);
  assert.equal(grid.columns[0].short, '0:00');
  assert.equal(grid.columns[0].label, '08:00:00–08:00:04');
  const placed = [...grid.cells.values()].reduce((sum, cell) => sum + cell.events.length, 0);
  assert.equal(placed, 90);
});

test('contagious words come from one agent and reach others in later stages, ranked by agents reached', () => {
  const events = [added('a'), added('b'), added('c'), added('d'),
    message(1, null, 'Task', 'Explain gravity carefully'),
    message(2, 'a', 'Round 1', 'gravity bends spacetime, says Zorvak; consider tides'),
    message(3, 'b', 'Round 1', 'gravity pulls mass; consider tides'),
    message(4, 'c', 'Round 1', 'gravity is curvature'),
    message(5, 'b', 'Round 2', 'Zorvak was right about spacetime'),
    message(6, 'c', 'Round 2', 'agree with zorvak'),
    message(7, 'c', 'Round 3', 'spacetime and tides'),
    message(8, 'd', 'Round 3', 'final: zorvak')];
  const ranked = contagiousTokens(spreadGrid(events));
  assert.deepEqual(ranked.map(({ token, reached }) => [token, reached]), [['zorvak', 3], ['spacetime', 2]]);
  assert.equal(ranked[0].row, 1);
});

test('the scan keeps at most six detail requests in flight and skips previews that are already complete', async () => {
  const long = 'x'.repeat(260);
  const events = Array.from({ length: 20 }, (_, index) => message(index + 1, 'a', null, long));
  events.push(message(21, 'a', null, 'short and complete'));
  let inFlight = 0, peak = 0;
  const requested = [];
  const loadDetail = async (event) => {
    requested.push(event.id);
    peak = Math.max(peak, ++inFlight);
    await new Promise(resolve => setTimeout(resolve, 1));
    inFlight -= 1;
    return { id: event.id, data: { content: `Full ${event.id}` } };
  };
  await scanTexts(events, loadDetail, () => {}).done;
  assert.equal(peak, 6);
  assert.equal(requested.length, 20);
  await scanTexts(events, loadDetail, () => {}).done;
  assert.equal(requested.length, 20, 'loaded texts are cached across scans');
});

test('cells after the cursor are dimmed and a cell click selects its first match', async () => {
  const root = document.createElement('div');
  const toolbar = document.createElement('div');
  const selected = [];
  const events = run();
  const view = phraseSpread.mount(root, { select: event => selected.push(event), selectAgent() {},
    loadDetail: async event => ({ id: event.id, data: { content: event.preview } }) }, toolbar);
  view.update({ events, cursor: 4, selectedId: null, agents });
  const input = toolbar.querySelector('input');
  input.value = 'zorvak';
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter' }));
  const cell = (row, column) => root.querySelectorAll('.ps-grid > .ps-cell')[row * 4 + column];
  assert.equal(cell(1, 1).classList.contains('is-future'), false);
  assert.equal(cell(2, 2).classList.contains('is-future'), true);
  assert.equal(root.querySelectorAll('.ps-mark').length, 3);
  assert.equal(root.querySelectorAll('.ps-mark.is-future').length, 2);
  assert.match(root.querySelector('.ps-summary').textContent,
    /^Appears in 4 of 7 messages · first by Debater 0 in Round 1 · reached 3 agents$/);
  view.update({ events, cursor: 7, selectedId: events[7].id, agents });
  assert.equal(cell(2, 2).classList.contains('is-future'), false);
  assert.equal(cell(2, 2).classList.contains('is-selected'), true);
  cell(2, 2).click();
  assert.equal(selected[0].position, 5);
  view.destroy();
  assert.equal(toolbar.children.length, 0);
});
