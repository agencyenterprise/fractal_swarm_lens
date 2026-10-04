import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'), { url: 'http://localhost/' });
for (const key of ['window', 'document', 'location', 'FormData']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const { installTimeline } = await import('../../src/swarm_lens/web/timeline-report.js');
const { Reports } = await import('../../src/swarm_lens/web/reports.js');

const settle = () => new Promise((resolve) => setTimeout(resolve, 15));
const manifest = {
  id: 'timeline', title: 'Misalignment detection', api_prefix: '/api/plugins/timeline', ui: { renderer: 'timeline' },
  model: { model: 'fake-model', ready: true, reason: null },
  config: { properties: {
    method: { type: 'string', enum: ['orchestrated', 'single', 'goal_tree'], default: 'orchestrated' },
    chunk_tokens: { type: 'integer', minimum: 1, default: 500000 },
  } },
};
const milestone = (position, title, extra = {}) => ({ positions: [position], title, description: `${title} happened.`,
  agents: ['Agent A'], kind: 'progress', severity: 0, misaligned: false, ...extra });
const job = (overrides = {}) => ({
  id: 'job1', plugin_id: 'timeline', plugin_version: '0.3.0', branch_id: 'b1', cursor: 40, status: 'completed',
  created_at: '2026-10-04T08:00:00Z', config: { method: 'single' }, model: { model: 'fake-model' },
  analysis: { input_digest: 'abc', output: {
    method: 'single', config: { method: 'single' }, events_read: 40, windows: 1, seconds: 12.5,
    usage: { calls: 1, failed_calls: 0, input_tokens: 12000, output_tokens: 900 }, citations: {}, calls: [],
    milestones: [
      milestone(3, 'Plan agreed', { kind: 'decision' }),
      milestone(17, 'Hid the failing test', { positions: [17, 21], kind: 'misalignment', severity: 3, misaligned: true }),
      milestone(30, 'Report sent'),
    ],
    open_threads: [{ positions: [25, 28], note: 'Why was the log deleted?' }],
  } },
  ...overrides,
});

function install() {
  const actions = [], sources = [], navigation = [], opened = [];
  installTimeline(manifest, {
    selection: () => ({ branchId: 'b1', cursor: 40 }),
    addAction: (action) => actions.push(action),
    addReports: (source) => sources.push(source),
    openReport: async (record) => opened.push(record),
    showEvent: async (...args) => navigation.push(['event', ...args]),
    showSnapshot: async (...args) => navigation.push(['snapshot', ...args]),
  });
  return { actions, sources, navigation, opened };
}

function render(record) {
  const installed = install();
  const root = document.createElement('article');
  installed.sources[0].render(root, record);
  return { root, ...installed };
}

function server(t, respond) {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body);
    requests.push({ path, body });
    return { ok: true, status: 200, json: async () => respond(path, body) };
  });
  return requests;
}

test('installs a Plugins menu action and a Reports source from the manifest', () => {
  const { actions, sources } = install();
  assert.deepEqual(actions.map((action) => [action.id, action.label]), [['timeline-analyze', 'Long-context LLM judge…']]);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].title, 'Timeline');
  assert.equal(sources[0].prefix, '/plugins/timeline');
});

test('the start dialog previews for free with the manifest defaults and starts the chosen method', async (t) => {
  const requests = server(t, (path, body) => path.endsWith('/preview')
    ? { events: 40, estimated_tokens: 12345, windows: body.config.method === 'orchestrated' ? 3 : 1,
      ready: true, reason: null, config: body.config, model: { model: 'fake-model' } }
    : { id: 'new', branch_id: 'b1' });
  const { actions, opened } = install();
  actions[0].onClick();
  await settle();
  assert.equal(document.querySelector('#dialog-title').textContent, 'Long-context LLM judge');
  assert.deepEqual(requests[0], { path: '/api/plugins/timeline/preview',
    body: { branch_id: 'b1', cursor: 40, config: { method: 'orchestrated', chunk_tokens: 500000 } } });
  const check = document.querySelector('.analysis-check');
  assert.equal(check.dataset.state, 'ready');
  assert.match(check.textContent, /Ready · fake-model/);
  assert.match(check.textContent, /Events40Estimated tokens12,345Chunks3/);
  assert.equal(document.querySelector('#confirm-dialog').disabled, false);

  [...document.querySelectorAll('#dialog-content .segmented button')].find((node) => node.textContent === 'Single').click();
  await settle();
  assert.equal(document.querySelector('.tlr-chunk-field').hidden, true);
  assert.deepEqual(requests.at(-1).body.config, { method: 'single' });
  document.querySelector('#dialog-form').requestSubmit();
  await settle();
  assert.deepEqual(requests.at(-1), { path: '/api/plugins/timeline/analyses',
    body: { branch_id: 'b1', cursor: 40, config: { method: 'single' } } });
  assert.deepEqual(opened, [{ id: 'new', branch_id: 'b1' }]);
});

test('a preview that is not ready keeps Analyze disabled and shows the reason', async (t) => {
  server(t, (path, body) => ({ events: 0, estimated_tokens: 0, windows: 0, ready: false,
    reason: 'Configure OPENAI_API_KEY on the server.', config: body.config, model: { model: 'fake-model' } }));
  install().actions[0].onClick();
  await settle();
  assert.equal(document.querySelector('.analysis-check').dataset.state, 'error');
  assert.match(document.querySelector('.analysis-check').textContent, /Configure OPENAI_API_KEY/);
  assert.equal(document.querySelector('#confirm-dialog').disabled, true);
});

test('a completed report shows the reviewer workload, flags by severity, and open threads', () => {
  const { root } = render(job());
  assert.match(root.querySelector('.report-title').textContent, /Timeline.*Completed/);
  assert.match(root.querySelector('.report-title p').textContent, /Single · events 1–40 · fake-model/);
  const [flags] = root.querySelectorAll('.report-stat');
  assert.match(flags.textContent, /^1Flags to review/);
  assert.equal(flags.querySelector('strong').className, 'alarming');
  const items = [...root.querySelectorAll('.tlr-milestone')];
  assert.deepEqual(items.map((item) => item.querySelector('.tlr-position').textContent), ['#3', '#17', '#30']);
  const flagged = root.querySelector('.tlr-milestone.flagged');
  assert.equal(flagged.dataset.severity, '3');
  assert.equal(flagged.querySelector('.tlr-severity').getAttribute('aria-label'), 'Severity 3 of 3');
  assert.equal(items[0].querySelector('.badge').textContent, 'Decision');
  assert.match(root.querySelector('.tlr-thread').textContent, /Why was the log deleted\?#25#28/);
});

test('flags only hides milestones that are not misaligned', () => {
  const { root } = render(job());
  const [all, flagsOnly] = root.querySelectorAll('.tlr-section-header .segmented button');
  assert.equal(flagsOnly.textContent, 'Flags only · 1');
  flagsOnly.click();
  assert.equal(flagsOnly.getAttribute('aria-pressed'), 'true');
  assert.deepEqual([...root.querySelectorAll('.tlr-milestone')].map((item) => item.hidden), [true, false, true]);
  all.click();
  assert.ok([...root.querySelectorAll('.tlr-milestone')].every((item) => !item.hidden));
});

test('flags only explains an empty result', () => {
  const record = job();
  record.analysis.output.milestones = record.analysis.output.milestones.filter((item) => !item.misaligned);
  const { root } = render(record);
  assert.equal(root.querySelector('.tlr-section > p.tlr-none:last-child').hidden, true);
  root.querySelectorAll('.tlr-section-header .segmented button')[1].click();
  assert.equal(root.querySelector('.tlr-section > p.tlr-none:last-child').hidden, false);
});

test('cited event chips jump the explorer to that event of the analyzed branch', async () => {
  const { root, navigation } = render(job());
  const chips = root.querySelectorAll('.tlr-milestone.flagged .tlr-chip');
  assert.deepEqual([...chips].map((chip) => chip.getAttribute('aria-label')),
    ['Open event 17 in the explorer', 'Open event 21 in the explorer']);
  chips[1].click();
  root.querySelector('.tlr-thread .tlr-chip').click();
  await settle();
  assert.deepEqual(navigation, [['event', 'b1', 21], ['event', 'b1', 25]]);
});

test('running and failed jobs show progress, the error, its detail, and the spent usage', () => {
  assert.match(render(job({ status: 'running', analysis: undefined })).root.querySelector('.report-progress').textContent,
    /Building a timeline of events 1–40 with fake-model/);
  const { root } = render(job({ status: 'failed', analysis: undefined, error: 'Timeline analysis failed; no milestones were inferred.',
    error_detail: 'IncompleteResponse: length', usage: { calls: 2, failed_calls: 1, input_tokens: 5000, output_tokens: null } }));
  assert.match(root.querySelector('.report-title').textContent, /Failed/);
  const failure = root.querySelector('.tlr-failure');
  assert.match(failure.textContent, /no milestones were inferred/);
  assert.match(failure.textContent, /2 model calls · 5K input · — output tokens/);
  assert.equal(failure.querySelector('details pre').textContent, 'IncompleteResponse: length');
});

test('the Reports tab lists every source newest first and renders the selected job with its source', async (t) => {
  const timelineJob = job({ id: 't1', created_at: '2026-10-04T09:00:00Z' });
  const mastJob = { id: 'm1', plugin_id: 'mast', branch_id: 'b1', cursor: 12, status: 'failed', created_at: '2026-10-04T08:00:00Z' };
  server(t, (path) => ({ jobs: path.startsWith('/api/plugins/mast/') ? [mastJob] : [timelineJob] }));
  const panel = document.createElement('section');
  const rendered = [], params = [];
  const reports = new Reports({ selection: () => ({ branchId: 'b1' }), registerView: () => panel,
    openView: () => {}, setViewParams: (...args) => params.push(args), openMenu: () => {} });
  reports.add({ plugin: 'mast', title: 'MAST', prefix: '/plugins/mast', render: (root, record) => rendered.push(record.id) });
  reports.add({ plugin: 'timeline', title: 'Timeline', prefix: '/plugins/timeline', render: (root, record) => rendered.push(record.id) });
  await reports.show({});
  const items = [...panel.querySelectorAll('.report-list-item')];
  assert.deepEqual(items.map((item) => item.querySelector('strong').textContent), ['Timeline', 'MAST']);
  assert.deepEqual(items.map((item) => item.querySelector('.badge').textContent), ['Completed', 'Failed']);
  assert.match(items[0].href, /view=reports&report=t1&plugin=timeline/);
  assert.deepEqual(rendered, ['t1']);
  assert.deepEqual(params, [['reports', { report: 't1', plugin: 'timeline' }]]);
  reports.stop();
});
