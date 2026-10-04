import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'), { url: 'http://localhost/' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.FormData = dom.window.FormData;
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const { installHttpCalls } = await import('../../src/swarm_lens/web/http-calls.js');

const settle = () => new Promise(resolve => setTimeout(resolve, 15));
const manifest = { id: 'http_calls', title: 'Network activity', api_prefix: '/api/plugins/http_calls', ui: { renderer: 'http_calls' } };
const bins = [0, 1, 2, 3].map(index => ({ start: `2026-01-01T00:0${index}:00+00:00`, end: `2026-01-01T00:0${index + 1}:00+00:00` }));
const domain = (name, counts, extra = {}) => ({
  domain: name, total: counts.reduce((sum, count) => sum + count, 0), reported: 0, counts, spike_bins: [],
  peak_bin: counts.indexOf(Math.max(...counts)), peak_count: Math.max(...counts),
  first_seen: { position: 4, event_id: `${name}-first`, at: bins[0].start }, ...extra,
});
const output = (overrides) => ({ axis: 'time', axis_reason: 'event timestamps (occurred_at)', bins, domains: [], spikes: [],
  total_calls: 0, spike_rule: 'A bin is a spike when…', interpretation: 'Not verified traffic.', ...overrides });

function open(record) {
  const requests = [], navigation = [], actions = [];
  globalThis.fetch = async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return { ok: true, status: 200, json: async () => record };
  };
  installHttpCalls(manifest, {
    selection: () => ({ branchId: 'b1', cursor: 30, name: 'Browsing run' }),
    addAction: action => actions.push(action),
    showEvidenceEvent: async (...args) => navigation.push(args),
  });
  assert.equal(actions[0].label, 'HTTP calls per website…');
  actions[0].onClick();
  return { requests, navigation };
}

test('modal charts calls per website, ranks websites, and a spike jumps to its first event', async () => {
  const github = domain('api.github.com', [2, 2, 2, 2]);
  const burst = domain('evil.example', [0, 0, 8, 0], { spike_bins: [2] });
  const spike = { domain: 'evil.example', first_bin: 2, last_bin: 2, count: 8, peak_count: 8, threshold: 4, ...bins[2],
    events: [{ position: 17, event_id: 'burst-0', at: bins[2].start }] };
  const { requests, navigation } = open({ branch_id: 'b1', cursor: 30,
    output: output({ total_calls: 16, domains: [github, burst], spikes: [spike] }) });
  await settle();

  assert.deepEqual(requests, [['/api/plugins/http_calls/analyses', { branch_id: 'b1', cursor: 30 }]]);
  const content = document.querySelector('#dialog-content');
  assert.equal(document.querySelector('#dialog-title').textContent, 'HTTP calls per website');
  assert.equal(content.querySelectorAll('.http-chart .http-line').length, 2);
  assert.match(content.textContent, /16 calls to 2 websites/);
  const rows = [...content.querySelectorAll('.http-table tbody tr')];
  assert.deepEqual(rows.map(row => row.querySelector('.http-site').textContent), ['api.github.com', 'evil.example']);
  assert.equal(rows[1].querySelector('.badge').textContent, 'Spike');
  const marker = content.querySelector('.http-spike');
  assert.match(marker.getAttribute('aria-label'), /evil\.example, 8 calls in 00:02:00–00:03:00/);

  marker.dispatchEvent(new dom.window.Event('click'));
  assert.deepEqual(navigation, [['b1', 30, 'burst-0']]);
  assert.equal(document.querySelector('#dialog').open, false);
});

test('a run without HTTP calls shows the empty state', async () => {
  open({ branch_id: 'b1', cursor: 30, output: output({}) });
  await settle();
  const content = document.querySelector('#dialog-content');
  assert.equal(content.querySelector('.http-empty').textContent, 'No HTTP calls recorded in this run');
  assert.equal(content.querySelector('.http-chart'), null);
});
