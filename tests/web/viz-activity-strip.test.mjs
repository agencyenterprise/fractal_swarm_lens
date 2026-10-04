import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'HTMLElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });

const { activityModel, availableMetrics, activityStrip } = await import('../../src/swarm_lens/web/viz/activity-strip.js');

const base = Date.parse('2026-10-03T08:45:00Z');
let position = 0;
const event = (kind, agent, stage, extra = {}) => {
  position += 1;
  return { id: `e${position}`, position, kind, agent_id: agent, at: new Date(base + position * 1000).toISOString(),
    stage_label: stage, preview: 'x'.repeat(10), ...extra };
};
const agentAdded = (id) => event('agent.added', id, null, { agent_name: id.toUpperCase() });

// Three debaters speak each round; B goes quiet in rounds 4-5 and A writes a long message in round 3.
function debate() {
  position = 0;
  const events = [agentAdded('a'), agentAdded('b'), agentAdded('c')];
  for (let round = 1; round <= 6; round++)
    for (const agent of ['a', 'b', 'c']) {
      if (agent === 'b' && (round === 4 || round === 5)) continue;
      events.push(event('message.created', agent, `Round ${round} · output`));
      if (agent === 'a' && round === 2) events.push(event('message.created', agent, `Round ${round}`));
    }
  return events;
}

const details = (entries) => new Map(Object.entries(entries));

test('cells aggregate per stage and scale against each agent\'s own median', () => {
  const events = debate();
  const model = activityModel(events, 'messages', new Map());
  assert.deepEqual(model.columns.map((column) => column.label), ['Round 1', 'Round 2', 'Round 3', 'Round 4', 'Round 5', 'Round 6']);
  const a = model.rows.find((row) => row.agent.id === 'a');
  assert.equal(a.agent.name, 'A');
  assert.deepEqual(a.cells.map((cell) => cell.value), [1, 2, 1, 1, 1, 1]);
  assert.equal(a.median, 1);
  assert.equal(a.cells[1].ratio, 2);
  assert.equal(model.cellOf.get(a.cells[1].events[1].id).column, 1);
});

test('length spikes over 2x the agent median are flagged once full content is loaded', () => {
  const events = debate();
  const aRound3 = events.find((item) => item.agent_id === 'a' && item.stage_label?.startsWith('Round 3'));
  const loaded = Object.fromEntries(events.filter((item) => item.kind === 'message.created')
    .map((item) => [item.id, { length: item === aRound3 ? 3100 : 1000 }]));
  const model = activityModel(events, 'length', details(loaded));
  const spike = model.flags.find((flag) => flag.kind === 'spike');
  assert.equal(spike.text, 'A 3.1× usual length in Round 3');
  assert.equal(spike.event, aRound3);

  const preview = activityModel(events, 'length', new Map());
  assert.ok(preview.rows.every((row) => row.cells.every((cell) => !cell.events.length || !cell.exact)));
  assert.ok(!preview.flags.some((flag) => flag.kind === 'spike'));
});

test('an agent that usually speaks is flagged when silent while others speak', () => {
  const model = activityModel(debate(), 'messages', new Map());
  const silences = model.flags.filter((flag) => flag.kind === 'silence');
  assert.deepEqual(silences.map((flag) => flag.text), ['B silent for 2 stages from Round 4']);
  assert.equal(silences[0].column, 3);
});

test('a stage that belongs to another role is not silence for anyone', () => {
  const events = [...debate(), event('message.created', 'judge', 'Final')];
  const model = activityModel(events, 'messages', new Map());
  assert.deepEqual(model.flags.filter((flag) => flag.kind === 'silence').map((flag) => flag.text), ['B silent for 2 stages from Round 4']);
  assert.equal(model.columns.at(-1).label, 'Final');
});

test('runs without stage labels fall back to equal time buckets', () => {
  const events = debate().map((item) => ({ ...item, stage_label: null }));
  const model = activityModel(events, 'messages', new Map());
  assert.equal(model.unit, 'intervals');
  assert.equal(model.columns.length, 17, "one bucket per message when there are fewer than 24");
  const total = model.rows.flatMap((row) => row.cells).reduce((sum, cell) => sum + cell.events.length, 0);
  assert.equal(total, events.filter((item) => item.kind === 'message.created').length);
});

test('tokens and latency are offered only when loaded details carry them', () => {
  const events = debate();
  const first = events.find((item) => item.kind === 'message.created');
  assert.deepEqual(availableMetrics(events, new Map()), ['messages', 'length']);
  assert.deepEqual(availableMetrics(events, details({ [first.id]: { length: 5, outputTokens: 40 } })), ['messages', 'length', 'tokens']);
  const withTools = [...events, event('tool.started', 'a', 'Round 6')];
  assert.deepEqual(availableMetrics(withTools, details({ [first.id]: { length: 5, latency: 1.2 } })), ['messages', 'length', 'latency', 'tools']);
});

test('the view loads details lazily, at most six at a time, and selects a cell\'s first event', async () => {
  const events = debate();
  const root = document.createElement('div');
  const toolbar = document.createElement('div');
  const pending = [];
  const selected = [];
  const actions = {
    seek() {}, selectAgent() {}, select: (item) => selected.push(item),
    loadDetail: (item) => new Promise((resolve) => pending.push(() => resolve({ data: { content: 'y'.repeat(50),
      metadata: { usage: { output_tokens: 12 }, latency_seconds: 2 } } }))),
  };
  const view = activityStrip.mount(root, actions, toolbar);
  const context = { events, cursor: 9, selectedId: null, agents: {}, state: {}, branch: {} };
  view.update(context);
  assert.equal(pending.length, 3, 'Messages only probes the first message of each agent');
  assert.deepEqual([...toolbar.querySelectorAll('.act-metrics button')].map((node) => node.textContent), ['Messages', 'Length']);
  pending.splice(0).forEach((resolve) => resolve());
  await new Promise((resolve) => setTimeout(resolve, 200));
  view.update(context);
  assert.ok([...toolbar.querySelectorAll('.act-metrics button')].some((node) => node.textContent === 'Tokens'));

  [...toolbar.querySelectorAll('.act-metrics button')].find((node) => node.textContent === 'Length').click();
  assert.equal(pending.length, 6);
  assert.match(toolbar.querySelector('.act-status').textContent, /Loading details 3\/17/);

  const cell = root.querySelector('.act-cell[data-row="0"][data-column="1"]');
  cell.click();
  assert.equal(selected[0].id, events.find((item) => item.agent_id === 'a' && item.stage_label?.startsWith('Round 2')).id);
  view.destroy();
  assert.equal(toolbar.children.length, 0);
});
