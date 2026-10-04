import test from 'node:test';
import assert from 'node:assert/strict';
import { columnScheme, MAX_COLUMNS } from '../../src/swarm_lens/web/viz/columns.js';

const start = Date.parse('2026-09-25T06:25:00Z');
const event = (position, kind, stage, seconds = position) => ({ id: `e${position}`, position, kind,
  at: new Date(start + seconds * 1000).toISOString(), stage_label: stage });
const isMessage = (item) => item.kind === 'message.created';

// Messageboard: 50 messages over three minutes; only 9 carry event-marker labels, right at the start and end.
function messageboard() {
  const events = [];
  for (let position = 1; position <= 216; position++) {
    const kind = position % 4 === 0 && position <= 200 ? 'message.created' : 'observation.recorded';
    events.push(event(position, kind, null, position * 0.85));
  }
  const messages = events.filter(isMessage);
  [[0, 'Task injection'], [1, 'Task injection'], [2, 'Task injection'], [3, 'Assignment loaded'], [4, 'Assignment loaded'],
    [5, 'Assignment loaded'], [47, 'World event'], [48, 'World event'], [49, 'World event']]
    .forEach(([index, stage]) => (messages[index].stage_label = stage));
  return events;
}

test('sparse event-marker labels give equal time buckets, with the labels kept as markers', () => {
  const events = messageboard();
  const scheme = columnScheme(events, isMessage);
  assert.equal(scheme.unit, 'intervals');
  assert.equal(scheme.columns.length, 14);
  const sizes = scheme.columns.map((_, index) => [...scheme.columnOf.values()].filter((column) => column === index).length);
  assert.equal(sizes.reduce((sum, size) => sum + size, 0), 50);
  assert.ok(Math.max(...sizes) <= 5, `no column swallows the run: ${sizes}`);
  assert.equal(scheme.columns[0].short, '0:00');
  assert.deepEqual(scheme.columns[0].markers, ['Task injection', 'Assignment loaded']);
  assert.deepEqual(scheme.columns.at(-1).markers, ['World event']);
});

test('a debate where every message sits in a round uses the rounds, with numbered short labels', () => {
  const events = [event(1, 'message.created', 'Original task')];
  for (let round = 1; round <= 20; round++)
    for (const offset of [0, 1, 2]) {
      const position = events.length + 1;
      events.push(event(position, 'message.created', `Debate round ${round} · output`));
      if (offset === 0) events.push(event(position + 1, 'memory.written', null));
    }
  const scheme = columnScheme(events, isMessage);
  assert.equal(scheme.unit, 'stages');
  assert.equal(scheme.columns.length, 21);
  assert.deepEqual(scheme.columns.slice(0, 3).map((column) => column.short), ['Original task', 'Debate round 1', '2']);
  const third = events.find((item) => item.stage_label === 'Debate round 2 · output');
  assert.equal(scheme.columnOf.get(third.id), 2);
  assert.ok(!scheme.columnOf.has(events.find((item) => item.kind === 'memory.written').id), 'only counted events are placed');
});

test('a few unlabeled events in a staged run join the nearest preceding stage', () => {
  const events = Array.from({ length: 10 }, (_, index) =>
    event(index + 1, 'message.created', index === 4 || index === 0 ? null : `Round ${Math.ceil((index + 1) / 3)}`));
  const scheme = columnScheme(events, isMessage);
  assert.equal(scheme.unit, 'stages');
  assert.deepEqual(scheme.columns.map((column) => column.label), ['Round 1', 'Round 2', 'Round 3', 'Round 4']);
  assert.equal(scheme.columnOf.get('e1'), 0, 'an unlabeled first event joins the first stage');
  assert.equal(scheme.columnOf.get('e5'), 1);
});

test('more than 60 stages are grouped into consecutive ranges', () => {
  const events = Array.from({ length: 150 }, (_, index) => event(index + 1, 'message.created', `Turn ${index + 1}`));
  const scheme = columnScheme(events, isMessage);
  assert.equal(scheme.columns.length, 50);
  assert.ok(scheme.columns.length <= MAX_COLUMNS);
  assert.equal(scheme.columns[0].label, 'Turn 1 – Turn 3');
  assert.equal(scheme.columns[0].short, '1–3');
  assert.equal(scheme.columnOf.get('e4'), 1);
  assert.equal(scheme.columns[1].start, 4);
});

test('never more columns than counted events', () => {
  for (const count of [1, 2, 5, 11]) {
    const events = Array.from({ length: count }, (_, index) => event(index + 1, 'message.created', null, index * 7));
    assert.equal(columnScheme(events, isMessage).columns.length, count);
  }
  const sameTime = Array.from({ length: 30 }, (_, index) => event(index + 1, 'message.created', null, 0));
  assert.equal(columnScheme(sameTime, isMessage).columns.length, 1);
  assert.deepEqual(columnScheme([event(1, 'observation.recorded', null)], isMessage).columns, []);
});

test('two stages are not phases, and an event without a time fails loudly', () => {
  const events = Array.from({ length: 20 }, (_, index) => event(index + 1, 'message.created', index < 10 ? 'Setup' : 'Run'));
  assert.equal(columnScheme(events, isMessage).unit, 'intervals');
  assert.throws(() => columnScheme([{ id: 'x', position: 1, kind: 'message.created', at: null, stage_label: null }]),
    /no valid time/);
});
