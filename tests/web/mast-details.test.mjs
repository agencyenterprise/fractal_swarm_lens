import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mastTraitDetails, modeDefinitions } from '../../src/swarm_lens/web/plugins/mast/static/details.js';
// The formatter loads lazily in the app; warm the module cache so renders settle within one tick here.
await import('../../src/swarm_lens/web/message-format.js');

const settle = () => new Promise(resolve => setTimeout(resolve, 15));
const job = { id: 'report', branch_id: 'child', cursor: 20 };
const label = { code: '1.3', label: 'Step Repetition', present: true };

function setup(t, fetchDetail) {
  const dom = new JSDOM('<!doctype html><body></body>');
  globalThis.document = dom.window.document;
  const requests = [], navigation = [];
  globalThis.fetch = async url => {
    requests.push(url);
    return fetchDetail();
  };
  const row = mastTraitDetails(label, job, {
    openTimeline: async (...args) => navigation.push(args),
  });
  document.body.append(row);
  t.after(() => dom.window.close());
  return { row, requests, navigation };
}

test('disclosure loads once, renders recorded text safely, and opens exact branch/event', async t => {
  const { row, requests, navigation } = setup(t, () => ({ ok: true, json: async () => ({
    explanation: 'Two repeats followed by a correction.', definition: 'Repeated steps.', occurrences: [{
      start_position: 5, end_position: 9, explanation: 'The correction is included.', events: [
        { event_id: 'e5', position: 5, sender: 'Agent A', at: '2026-10-04T00:00:00Z', role: 'supporting', text: '<img src=x onerror=alert(1)>' },
        { event_id: 'e7', position: 7, sender: 'Agent B', at: '2026-10-04T00:00:01Z', role: 'context', text: 'Please reconsider.' },
        { event_id: 'e9', position: 9, sender: 'Agent A', at: '2026-10-04T00:00:02Z', role: 'counterevidence', text: 'I retract that.' },
      ],
    }],
  }) }));
  assert.equal(row.open, false);
  assert.equal(requests.length, 0);
  row.open = true;
  await settle();
  assert.deepEqual(requests, ['/api/plugins/mast/analyses/report/traits/1.3']);
  assert.match(row.textContent, /events 5–9/);
  assert.equal(row.querySelector('img'), null);
  assert.match(row.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(row.textContent, /Counterevidence/);
  assert.equal(row.querySelector('.context details').open, false);
  assert.equal(row.querySelector('.counterevidence details').open, false);
  assert.equal(row.querySelector('.counterevidence').hidden, false);
  assert.equal(row.querySelector('.context').hidden, true);
  assert.match(row.querySelector('.mast-evidence-controls').textContent, /1 supporting · 1 counterevidence · 1 context/);
  row.querySelector('.mast-evidence-controls button').click();
  assert.equal(row.querySelector('.context').hidden, false);
  row.querySelector('.mast-evidence-controls button').click();
  assert.equal(row.querySelector('.context').hidden, true);
  row.querySelector('.supporting .mast-event-link').click();
  await settle();
  assert.deepEqual(navigation, [['child', 5, 'e5']]);
  row.open = false;
  await settle();
  row.open = true;
  await settle();
  assert.equal(requests.length, 1);
});

test('message reader formats Markdown and math, preserves original text, and opens context on demand', async t => {
  const text = String.raw`1. **First month**: Three times the initial number.
   \[
   3 \times 20 = 60
   \]

2. **Second month**: Twenty fewer cards.`;
  const { row } = setup(t, () => ({ ok: true, json: async () => ({ occurrences: [{
    start_position: 5, end_position: 6, explanation: 'A repeated calculation.', events: [
      { event_id: 'e5', position: 5, sender: 'A', at: '2026-10-04T00:00:00.123456Z', role: 'supporting', text },
      { event_id: 'e6', position: 6, sender: 'A', at: '2026-10-04T00:00:01Z', role: 'context', text: '{"status":"done"}' },
    ],
  }] }) }));
  row.open = true;
  await settle();
  const content = row.querySelector('.supporting .mast-message-content');
  assert.equal(content.querySelectorAll('ol > li').length, 2);
  assert.equal(content.querySelector('strong').textContent, 'First month');
  assert.equal(content.querySelectorAll('math').length, 1);
  assert.match(content.querySelector('math').textContent, /×/);
  assert.equal(row.querySelector('time').textContent, '2026-10-04 · 00:00:00 UTC');
  assert.equal(row.querySelector('time').dataset.tip, '2026-10-04T00:00:00.123456Z');
  const original = row.querySelector('.supporting .mast-message-tools button');
  original.click();
  assert.equal(content.querySelector('pre').textContent, text);
  assert.equal(original.getAttribute('aria-pressed'), 'true');
  original.click();
  await settle();
  assert.equal(content.querySelectorAll('math').length, 1);
  const context = row.querySelector('.context');
  assert.equal(context.querySelector('.mast-message-content').textContent, '');
  row.querySelector('.mast-evidence-controls button').click();
  context.querySelector('details').open = true;
  await settle();
  assert.equal(context.querySelector('pre').textContent, '{\n  "status": "done"\n}');
});

test('old reports show an honest empty state and shared summary without invented evidence', async t => {
  const { row } = setup(t, () => ({ ok: true, json: async () => ({
    notice: 'Evidence unavailable for this saved judgment.', summary: 'Shared assessment summary.', occurrences: [],
  }) }));
  row.open = true;
  await settle();
  assert.match(row.textContent, /Evidence unavailable/);
  assert.match(row.textContent, /Overall assessment summary/);
  assert.equal(row.querySelectorAll('.mast-occurrence').length, 0);
});

test('a failed details request can be retried without replacing the trait verdict', async t => {
  let attempt = 0;
  const { row, requests } = setup(t, () => ++attempt === 1
    ? { ok: false, json: async () => ({ detail: 'Temporarily unavailable' }) }
    : { ok: true, json: async () => ({ notice: 'Evidence unavailable.', occurrences: [] }) });
  row.open = true;
  await settle();
  assert.match(row.textContent, /Temporarily unavailable/);
  assert.equal(row.querySelector('.mast-badge').textContent, 'Present');
  row.querySelector('.mast-trait-body button').click();
  await settle();
  assert.equal(requests.length, 2);
  assert.match(row.textContent, /Evidence unavailable/);
});

test('absent and unparsed traits also have accessible native disclosures', t => {
  const { row } = setup(t, () => { throw new Error('Should not load while closed'); });
  for (const present of [false, null]) {
    const trait = mastTraitDetails({ ...label, present }, job, {});
    row.after(trait);
    assert.equal(trait.tagName, 'DETAILS');
    assert.equal(trait.firstElementChild.tagName, 'SUMMARY');
    assert.equal(trait.querySelector('.mast-badge').textContent, present === null ? 'Unparsed' : 'Absent');
  }
});

test('failure-mode tooltips use the definition of the displayed name and flag upstream code swaps', () => {
  const definitions = modeDefinitions({ categories: [
    { code: '1.3', label: 'Step Repetition', definition_label: 'Step Repetition', definition: 'Repeats a finished step. More detail.\n\nExample.' },
    { code: '3.2', label: 'No or Incorrect Verification', definition_label: 'Weak Verification', definition: 'Checks exist but are shallow.' },
    { code: '3.3', label: 'Weak Verification', definition_label: 'No or Incorrect Verification', definition: 'Omission of proper checking.' },
  ] });
  assert.equal(definitions.get('1.3'), 'Repeats a finished step.');
  assert.match(definitions.get('3.2'), /^Omission of proper checking\.\nUpstream files give this name another code/);
  assert.match(definitions.get('3.3'), /^Checks exist but are shallow\./);
});
