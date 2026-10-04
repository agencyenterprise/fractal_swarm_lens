import test from 'node:test';
import assert from 'node:assert/strict';

let nodes;
let storage;
function resetDOM() {
  nodes = new Map();
  storage = new Map();
  globalThis.document = { querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      hidden: true, textContent: '', dataset: {}, classList: { add() {}, remove() {} }, setAttribute() {},
    });
    return nodes.get(selector);
  } };
  globalThis.sessionStorage = {
    getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
  };
}
resetDOM();
const { LiveView } = await import('../../src/swarm_lens/web/live.js');
const job = { id: 'execution-1', branch_id: 'child', branch_name: 'Test fork', input_cursor: 5,
  status: 'running', created_at: '2026-10-03T12:00:00Z', started_at: '2026-10-03T12:00:00Z' };
const done = { ...job, status: 'completed', output_cursor: 29, finished_at: '2026-10-03T12:00:07Z' };
function setup(t, selection = { branchId: 'child' }, forked = async () => {}) {
  resetDOM();
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: Date.parse(job.started_at) });
  return new LiveView({ selection: () => selection, forked, seek() {}, update: async () => {} });
}

test('running indicator animates while pending and stops on completion', t => {
  const view = setup(t);
  view.renderStatus(job);
  assert.equal(nodes.get('#live-spinner').hidden, false);
  assert.equal(nodes.get('#live-controls').dataset.status, 'running');
  t.mock.timers.tick(2000);
  assert.match(nodes.get('#live-elapsed').textContent, /^2s/);
  view.renderStatus(done);
  assert.equal(nodes.get('#live-spinner').hidden, true);
  assert.equal(nodes.get('#live-status').textContent, 'Run completed');
});

test('a fast job still notifies, and the banner survives the toast timeout', t => {
  const view = setup(t);
  view.watch({ ...job, status: 'queued' });
  view.finished(done); // No intermediate running message is required.
  assert.match(nodes.get('#execution-notice-text').textContent, /Test fork completed · 24 new events saved/);
  assert.equal(nodes.get('#execution-notice').hidden, false);
  t.mock.timers.tick(10000);
  assert.equal(nodes.get('#toast').hidden, true);
  assert.equal(nodes.get('#execution-notice').hidden, false);
  nodes.get('#dismiss-execution-notice').onclick();
  assert.equal(nodes.get('#execution-notice').hidden, true);
  // Replayed/stale WebSocket status must not trigger another notification.
  view.watch(job);
  view.finished(done);
  assert.equal(nodes.get('#execution-notice').hidden, true);
  view.renderStatus(job);
  assert.equal(nodes.get('#live-status').textContent, 'Run completed');
});

test('pending completion survives reload and changing the selected conversation', async t => {
  const original = setup(t);
  original.watch(job);
  clearTimeout(original.watchTimer);
  let opened;
  const restored = new LiveView({ selection: () => ({ branchId: 'other' }),
    forked: async id => { opened = id; }, seek() {}, update: async () => {} });
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => done }));
  await restored.pollWatched();
  assert.equal(nodes.get('#execution-notice').hidden, false);
  assert.deepEqual(JSON.parse(storage.get('swarm-lens-pending-executions')), []);
  await nodes.get('#view-execution-branch').onclick();
  assert.equal(opened, 'child');
});

test('failed jobs notify once and preserve the error for inspection', t => {
  const view = setup(t);
  const failed = { ...job, status: 'failed', error: 'Recorded events are preserved.' };
  view.watch(job);
  view.finished(failed);
  view.renderStatus(failed);
  assert.equal(nodes.get('#live-spinner').hidden, true);
  assert.equal(nodes.get('#execution-notice').dataset.outcome, 'error');
  assert.equal(nodes.get('#execution-error').hidden, false);
  assert.match(nodes.get('#execution-notice-text').textContent, /Test fork failed/);
});
