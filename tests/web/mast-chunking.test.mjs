import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('src/swarm_lens/web/index.html', `file://${process.cwd()}/`), 'utf8'), { url: 'http://localhost/' });
for (const key of ['window', 'document', 'HTMLElement', 'HTMLInputElement', 'Element', 'Node', 'Event', 'FormData', 'navigator', 'location']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const { install } = await import(new URL('src/swarm_lens/web/plugins/mast/static/index.js', `file://${process.cwd()}/`));
const settle = () => new Promise(resolve => setTimeout(resolve, 5));

function host() {
  let action, view;
  const root = document.createElement('section');
  document.body.append(root);
  install({ context: () => ({ branch: { id: 'branch' }, cursor: 3730 }),
    registerView: config => { view = config; return root; }, addAction: config => { action = config; },
    setViewParams() {}, openView() {}, openBranchView() {},
  }, { judge: { ready: true, model: 'test-model' } });
  return { root, action, view };
}

test('oversized preview offers chunked analysis and explains multiple requests before submission', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ can_analyze: true, strategy: 'chunked', chunk_count: 4, event_count: 3730, workers: 3 }) };
  });
  const { action } = host();
  action.onClick();
  await settle();
  assert.equal(document.querySelector('#confirm-dialog').disabled, false);
  assert.match(document.querySelector('.mast-size').textContent, /4 chunks/);
  assert.match(document.querySelector('.mast-size').textContent, /up to 3 workers/);
  assert.match(document.querySelector('.mast-dialog').textContent, /up to two requests per chunk/);
  assert.match(document.querySelector('.mast-dialog').textContent, /reconciliation requests/);
  assert.deepEqual(calls.map(c => c.path), ['/api/plugins/mast/preview']);
  document.querySelector('#dialog').close();
});

test('reports show reconciliation progress and preserved stage links on failure', async t => {
  const job = { id: 'job', branch_id: 'branch', cursor: 3730, judge: { model: 'test-model' },
    created_at: new Date().toISOString(), status: 'running', progress: { phase: 'reconciliation', total_chunks: 4, reconciliation_step: 2 },
    stages: [{ phase: 'chunk', index: 1, start_position: 1, end_position: 900, artifact: 'abc', summary: 'Saved finding.' }] };
  t.mock.method(globalThis, 'fetch', async path => ({ ok: true, json: async () => path.endsWith('/taxonomy') ? { categories: [] } : { jobs: [job] } }));
  const { root, view } = host();
  await view.onShow({ report: 'job' });
  assert.match(root.textContent, /Reconciling 4 chunks · step 2/);
  assert.match(root.textContent, /1 saved analysis stages/);
  assert.equal(root.querySelector('a[href="/api/artifacts/abc"]').textContent, 'Saved input and result (JSON)');
  view.onHide();
  job.status = 'failed';
  job.error = 'The model request timed out. Completed stages were retained.';
  job.provider_error = { http_status: 400, code: 'string_above_max_length', request_id: 'req_test123' };
  await view.onShow({ report: 'job' });
  assert.match(root.textContent, /No assessment was produced/);
  assert.match(root.textContent, /Provider diagnostics/);
  assert.match(root.textContent, /string_above_max_length/);
  assert.match(root.textContent, /Saved finding/);
  assert.equal(root.querySelector('.mast-progress'), null);
  view.onHide();
});


test('parallel progress shows active ranges and saved stages in chronological order', async t => {
  const job = { id: 'parallel-job', branch_id: 'branch', cursor: 3730, judge: { model: 'test-model' },
    created_at: new Date().toISOString(), status: 'running', progress: { phase: 'chunks', workers: 3,
      completed_chunks: 2, total_chunks: 9, active_chunks: [
        { index: 1, start_position: 1, end_position: 482 },
        { index: 4, start_position: 1433, end_position: 1904 },
      ] },
    stages: [3, 2].map(index => ({ phase: 'chunk', index, start_position: index * 100, end_position: index * 100 + 99, artifact: `stage-${index}`, summary: 'Saved.' })) };
  t.mock.method(globalThis, 'fetch', async path => ({ ok: true, json: async () => path.endsWith('/taxonomy') ? { categories: [] } : { jobs: [job] } }));
  const { root, view } = host();
  await view.onShow({ report: 'parallel-job' });
  assert.match(root.textContent, /2 of 9 saved/);
  assert.match(root.textContent, /up to 3 workers/);
  assert.match(root.textContent, /1 \(events 1–482\)/);
  assert.match(root.textContent, /4 \(events 1433–1904\)/);
  assert.deepEqual([...root.querySelectorAll('a[href^="/api/artifacts/stage-"]')].map(a => a.getAttribute('href')), ['/api/artifacts/stage-2', '/api/artifacts/stage-3']);
  view.onHide();
  job.progress.stopping = true;
  await view.onShow({ report: 'parallel-job' });
  assert.match(root.textContent, /Finishing in-flight calls after an error/);
  view.onHide();
});

test('completed chunked reports render through the shared findings format', async t => {
  const job = { id: 'completed-job', branch_id: 'branch', cursor: 3730, judge: { model: 'test-model' },
    created_at: new Date().toISOString(), status: 'completed', config: { completeness: 'complete' },
    analysis: { output: { format: 'swarm-lens.findings/v1', metrics: [], annotations: [], report: {
      summary: 'The reconciled assessment.', task_completed: true, labels: [], warnings: [], raw_response: 'Saved response',
      upstream: { revision: 'test-revision', upstream_notes: [] },
      chunking: { chunk_count: 4, reconciliation_steps: 1, limitation: 'Chunk summaries may omit distant context.' },
    } } } };
  t.mock.method(globalThis, 'fetch', async path => ({ ok: true, json: async () => path.endsWith('/taxonomy') ? { categories: [] } : { jobs: [job] } }));
  const { root, view } = host();
  await view.onShow({ report: job.id });
  assert.match(root.textContent, /4 chunks · 1 reconciliation steps/);
  assert.match(root.textContent, /The reconciled assessment/);
  assert.match(root.textContent, /Task completed/);
  assert.equal(root.querySelector('.mast-progress'), null);
  view.onHide();
});
