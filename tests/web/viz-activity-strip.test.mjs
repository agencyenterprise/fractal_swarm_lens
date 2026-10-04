import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'HTMLElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });

const { activityModel, availableMetrics, detailNumbers, activityStrip } = await import('../../src/swarm_lens/web/viz/activity-strip.js');

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
  assert.equal(model.columns.length, 12);
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

test('a run that records no token usage never offers Tokens, even with zero-filled usage blocks', () => {
  const events = debate();
  const messages = events.filter((item) => item.kind === 'message.created');
  const zeroUsage = { data: { content: 'hello', metadata: { round: 1, usage: { input_tokens: 0, output_tokens: 0 } } } };
  const noUsage = { data: { content: 'hello', metadata: { round: 1, stage_label: 'Round 1', recipient: 'b' } } };
  const loaded = new Map(messages.map((item, index) => [item.id, detailNumbers(index % 2 ? zeroUsage : noUsage)]));
  assert.deepEqual(availableMetrics(events, loaded), ['messages', 'length']);
  const measured = { data: { content: 'hello', metadata: { usage: { input_tokens: 90, output_tokens: 0 } } } };
  assert.equal(detailNumbers(measured).outputTokens, 0, 'a measured call may still produce zero output tokens');
});

test('cells whose loaded details lack the value show no data and stay out of medians and flags', () => {
  const events = debate();
  const messages = events.filter((item) => item.kind === 'message.created');
  // A records 100 tokens everywhere except a 900-token spike in round 3; B never records tokens.
  const loaded = new Map(messages.map((item) => {
    if (item.agent_id === 'b') return [item.id, { length: 10 }];
    const spike = item.agent_id === 'a' && item.stage_label.startsWith('Round 3');
    return [item.id, { length: 10, outputTokens: spike ? 900 : 100 }];
  }));
  const model = activityModel(events, 'tokens', loaded);
  const b = model.rows.find((row) => row.agent.id === 'b');
  const bCells = b.cells.filter((cell) => cell.events.length);
  assert.ok(bCells.every((cell) => cell.noData && cell.value === null && cell.ratio === null));
  assert.equal(b.median, null);
  const a = model.rows.find((row) => row.agent.id === 'a');
  assert.equal(a.median, 100);
  assert.deepEqual(model.flags.filter((flag) => flag.kind === 'spike').map((flag) => flag.agentId), ['a']);
  // B's only flag is its real absence in rounds 4-5, not anything read from its missing tokens.
  assert.deepEqual(model.flags.filter((flag) => flag.agentId === 'b').map((flag) => flag.text), ['B silent for 2 stages from Round 4']);

});

test('the view renders cells without the value as no data, not as zero', async () => {
  // Fresh ids keep this run's details out of the cache other tests rely on.
  const events = debate().map((item) => ({ ...item, id: `nodata-${item.id}` }));
  const loadDetail = async (item) => ({ data: { content: 'x'.repeat(20),
    metadata: item.agent_id === 'b' ? { round: 1 } : { usage: { input_tokens: 5, output_tokens: 100 } } } });
  const root = document.createElement('div');
  const toolbar = document.createElement('div');
  const view = activityStrip.mount(root, { seek() {}, selectAgent() {}, select() {}, loadDetail }, toolbar);
  const context = { events, cursor: 99, selectedId: null, agents: {}, state: {}, branch: {} };
  const tab = (title) => [...toolbar.querySelectorAll('.act-metrics button')].find((node) => node.textContent === title);
  view.update(context);
  await new Promise((resolve) => setTimeout(resolve, 200));
  view.update(context);
  tab('Tokens').click();
  await new Promise((resolve) => setTimeout(resolve, 300));
  view.update(context);
  const cells = (row) => [...root.querySelectorAll(`.act-cell[data-row="${row}"]:not(.is-empty)`)];
  assert.ok(cells(1).length > 0);
  assert.ok(cells(1).every((node) => node.classList.contains('is-nodata') && /: no data/.test(node.getAttribute('aria-label'))));
  assert.ok(cells(0).every((node) => !node.classList.contains('is-nodata') && /100 output tokens avg/.test(node.getAttribute('aria-label'))));
  tab('Messages').click();
  view.destroy();
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
