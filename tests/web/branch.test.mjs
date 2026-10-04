import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'), { url: 'http://localhost/' });
for (const key of ['window', 'document', 'HTMLElement', 'HTMLInputElement', 'Element', 'Node', 'Event', 'FormData']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const { branchDialog } = await import('../../src/swarm_lens/web/branch.js');
const { field } = await import('../../src/swarm_lens/web/dialog.js');
const $ = selector => document.querySelector(selector);
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const source = { branchId: 'parent', branchName: 'Original', cursor: 16, at: '2026-10-03T12:00:00Z', defaultName: 'Experiment' };

function setup(t, info = { can_execute: true, remaining_tasks: 2 }) {
  const requests = [], created = [], started = [];
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) });
    return { ok: true, json: async () => path.endsWith('/fork') ? { id: 'child' } : info };
  });
  const goal = field('Shared goal', 'goal', 'Original goal', 'textarea');
  branchDialog({ source, title: 'Change goal', fields: [goal],
    change: form => ({ kind: 'environment.updated', data: { goal: form.get('goal') } }),
    live: { enabled: true, startFork: async (...args) => started.push(args) },
    created: async id => created.push(id),
  });
  return { goal, requests, created, started };
}

test('save-only branching includes the edit at the visual cursor without running', async t => {
  const { goal, requests, created, started } = setup(t);
  await settle();
  assert.equal($('#alternate-dialog').disabled, false);
  assert.equal($('#alternate-dialog').hidden, false);
  assert.ok($('#alternate-dialog').classList.contains('primary'));
  assert.ok(!$('#confirm-dialog').classList.contains('primary'));
  assert.equal($('#dialog-content').querySelector('select'), null);
  goal.input.value = 'A changed goal';
  goal.input.dispatchEvent(new Event('input'));
  assert.equal($('#alternate-dialog').disabled, true);
  await settle();
  await $('#dialog-form').onsubmit(new Event('submit', { cancelable: true }));
  const save = requests.find(request => request.path.endsWith('/fork'));
  assert.equal(save.body.cursor, 16);
  assert.equal(save.body.intervention.data.goal, 'A changed goal');
  assert.deepEqual(created, ['child']);
  assert.equal(started.length, 0);
});

test('run choice rechecks final edits and passes the same cursor to the live executor', async t => {
  const { goal, requests, started } = setup(t);
  await settle();
  goal.input.value = 'Include violet';
  await $('#alternate-dialog').onclick();
  assert.equal(started.length, 1);
  const [parent, body, requestId] = started[0];
  assert.equal(parent, 'parent');
  assert.equal(body.cursor, 16);
  assert.equal(body.steps, 2);
  assert.equal(body.intervention.data.goal, 'Include violet');
  assert.ok(requestId);
  assert.equal(requests.at(-1).body.intervention.data.goal, 'Include violet');
  assert.equal(requests.some(request => request.path.endsWith('/fork')), false);
});

test('unsupported live positions keep save-only branching available without shifting cursor', async t => {
  const { requests, started } = setup(t, { can_execute: false, reason: 'The registered runtime version does not match.' });
  await settle();
  assert.equal($('#alternate-dialog').disabled, true);
  assert.equal($('#alternate-dialog').hidden, true);
  assert.equal($('#confirm-dialog').disabled, false);
  assert.ok($('#confirm-dialog').classList.contains('primary'));
  assert.match($('.fork-live').textContent, /version does not match/);
  await $('#dialog-form').onsubmit(new Event('submit', { cancelable: true }));
  assert.equal(requests.at(-1).body.cursor, 16);
  assert.equal(started.length, 0);
});

test('a message fork enables live execution and explains task restart at the same event', async t => {
  const { started } = setup(t, { can_execute: true, remaining_tasks: 3, next_task: 0, resume_mode: 'restart_task' });
  await settle();
  assert.equal($('#alternate-dialog').disabled, false);
  assert.match($('.fork-live').textContent, /new execution of task 1/);
  assert.match($('.fork-live').textContent, /fork stays at event 16/);
  await $('#alternate-dialog').onclick();
  assert.equal(started[0][1].cursor, 16);
});

test('imported trace preview shows execution order, models and preserved injection', async t => {
  const { started } = setup(t, { can_execute: true, remaining_tasks: 6, resume_mode: 'trace_continuation',
    summary: 'Debater 0 → Debater 1 → Debater 2 each round, then Aggregator.',
    next_actor: 'Debater 1', next_round: 19, models: ['gpt-4o-mini-2024-07-18'],
    injection_agents: ['debater_0'], tool_names: [] });
  await settle();
  assert.equal($('#alternate-dialog').disabled, false);
  assert.match($('.fork-live').textContent, /then Aggregator/);
  assert.match($('.fork-live-line').textContent, /next: Debater 1, round 19/);
  assert.match($('.fork-live').textContent, /Preserves the recorded input injection for debater_0/);
  assert.match($('.fork-live').textContent, /6 agent turns/);
  await $('#alternate-dialog').onclick();
  assert.equal(started[0][1].steps, 6);
});
