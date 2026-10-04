import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const ticks = async (n = 10) => { for (let i = 0; i < n; i++) await new Promise(resolve => setImmediate(resolve)); };
// x holds b throughout; y reads x1 and flips a -> b; arrows must start at the exact message read (x1).
const cell = (event_id, seq, stance, stated = true) => ({ event_id, seq, stance, stated, correct: null });
const report = {
  format: 'stance-lanes/v1', stance_source: 'regex', pattern: '\\(([A-Z])\\)', stance_question: null, answer_key: null,
  stances: ['b', 'a'], first_stances_differ: true, read_basis: ['delivered_sources'],
  typology: { kind: 'cascade', agents: ['x'], text: "The minority stance 'b' of x won over the others." },
  lanes: [
    { agent_id: 'x', flips: 0, challenged_messages: 1, holdout: true, cells: [cell('x0', 5, 'b'), cell('x1', 8, 'b')] },
    { agent_id: 'y', flips: 1, challenged_messages: 1, holdout: false, cells: [cell('y0', 6, 'a'), cell('y1', 9, 'b')] },
  ],
  flips: [{ agent_id: 'y', seq: 9, event_id: 'y1', from: 'a', to: 'b', toward: { x: 1 }, basis: 'delivered_sources',
            read: { x: { seq: 8, event_id: 'x1' } }, transition: null }],
};

async function page(t) {
  const html = await readFile(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8');
  const { window } = new JSDOM(html, { url: 'http://localhost/' });
  for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event', 'navigator', 'MutationObserver', 'FormData'])
    Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? window : window[key] });
  window.HTMLDialogElement.prototype.showModal ??= function () { this.open = true; };
  window.HTMLDialogElement.prototype.close ??= function () { this.open = false; };
  t.after(() => window.close());
  return window;
}

test('lanes draw one arrow per adoption from the read message, badge the holdout and name the pattern', async t => {
  await page(t);
  const { renderLanes, arrows } = await import('../../src/swarm_lens/plugins/stance_lanes/assets/lanes.js');
  assert.deepEqual(arrows(report).map(a => [a.peer, a.from, a.to]), [['x', { row: 0, column: 1 }, { row: 1, column: 1 }]]);
  const node = renderLanes(report, { open: () => {}, openRead: () => {}, fork: () => {} });
  assert.equal(node.querySelectorAll('.stance-arrow').length, 1);
  assert.equal(node.querySelector('.stance-arrow').dataset.flip, 'y1');
  assert.equal(node.querySelectorAll('.stance-cell.flip').length, 1);
  assert.match(node.querySelector('.stance-labels').textContent, /x.*holdout/);
  assert.match(node.querySelector('.stance-head').textContent, /cascade · x/);
  assert.match(node.querySelector('.stance-flip').textContent, /y · event 9 · a → b · toward x/);
});

test('flip actions open the exact events and fork at the event before the flip', async t => {
  const window = await page(t);
  const { flipActions } = await import('../../src/swarm_lens/plugins/stance_lanes/assets/index.js');
  const navigation = [], requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return { ok: true, status: 201, json: async () => ({ id: 'child' }) };
  };
  const actions = flipActions({ openTimeline: async (...args) => navigation.push(args) }, 'main');
  const [flip] = report.flips;
  actions.openRead(flip, 'x');
  actions.open(flip);
  t.mock.timers.enable({ apis: ['setTimeout'] });  // the success toast's hide timer must not outlive the page
  actions.fork(flip);
  assert.equal(window.document.querySelector('#field-name').value, 'Before y flips to b');
  window.document.querySelector('#confirm-dialog').click();
  await ticks();
  assert.deepEqual(requests, [['/api/branches/main/fork', { cursor: 8, name: 'Before y flips to b' }]]);
  assert.deepEqual(navigation, [['main', 8, 'x1'], ['main', 9, 'y1'], ['child', 8]]);
});

const completed = (end) => ({ status: 'completed', analysis: { end, output: { report } } });

test('a fork shows an ancestor analysis only if it ends at or before the fork', async t => {
  await page(t);
  const { latestReport } = await import('../../src/swarm_lens/plugins/stance_lanes/assets/index.js');
  const jobs = { child: [], parent: [completed(30), completed(12)], root: [completed(5)] };
  globalThis.fetch = async url => ({ ok: true, status: 200, json: async () => ({ jobs: jobs[url.split('/')[3]] }) });
  const branches = [{ id: 'child', parent_id: 'parent', fork_position: 20 }, { id: 'parent', parent_id: 'root', fork_position: 3 }, { id: 'root' }];
  assert.equal((await latestReport('child', branches)).job.analysis.end, 12);
  jobs.parent = [completed(30)];
  assert.equal(await latestReport('child', branches), null);  // root's analysis ends after the parent's fork at 3
});

test('a slow load for a branch left behind never replaces the newer view', async t => {
  await page(t);
  const { install } = await import('../../src/swarm_lens/plugins/stance_lanes/assets/index.js');
  const pending = {};
  globalThis.fetch = url => new Promise(resolve => { pending[url.split('/')[3]] = () => resolve({ ok: true, status: 200, json: async () => ({ jobs: [] }) }); });
  let context = { branch: { id: 'a' }, workspace: { branches: [] } }, onShow;
  const panel = document.createElement('section');
  install({ context: () => context, registerView: config => { onShow = config.onShow; return panel; }, addAction: () => {} });
  const slow = onShow();
  context = { branch: { id: 'b' }, workspace: { branches: [{ id: 'b', parent_id: null }] } };
  const fresh = onShow();
  pending.b();
  await fresh;
  panel.append(document.createElement('i'));
  pending.a();
  await slow;
  assert.ok(panel.querySelector('i'), 'the stale load for branch a replaced the panel');
});
