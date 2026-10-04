import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><section id="transcript"></section><div id="toast" hidden></div>', { url: 'http://localhost/' });
const observed = new Set();
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  localStorage: dom.window.localStorage,
  requestAnimationFrame: (callback) => callback(),
  IntersectionObserver: class {
    constructor(callback, options) { this.callback = callback; this.options = options; }
    observe(node) { observed.add({ node, observer: this }); }
    unobserve(node) { for (const entry of observed) if (entry.node === node) observed.delete(entry); }
  },
});
dom.window.HTMLElement.prototype.scrollIntoView = function () { globalThis.scrolledTo = this.dataset.id; };
const { Transcript } = await import('../../src/swarm_lens/web/transcript.js');
// The formatter loads lazily in the app; warm the module cache so formatting settles within one tick here.
await import('../../src/swarm_lens/web/message-format.js');
const tick = () => new Promise((resolve) => setTimeout(resolve));

const agents = { a: { id: 'a', name: 'Debater 0' }, b: { id: 'b', name: 'Debater 1' } };
const message = (position, agent, stage, preview) => ({ id: `e${position}`, position, kind: 'message.created',
  at: '2026-10-03T08:50:00Z', agent_id: agent, label: 'message.created', preview, stage_label: stage });
const events = [
  message(1, 'a', 'Debate round 1 · output', 'First'),
  { id: 'e2', position: 2, kind: 'observation.recorded', label: 'model_input', agent_id: 'a', preview: 'Prompt', stage_label: 'Debate round 1 · model input' },
  message(3, 'b', 'Debate round 1 · output', 'Second'),
  message(4, 'a', 'Debate round 2 · output', 'Third **preview**'),
  message(5, 'b', 'Debate round 2 · output', 'Fourth'),
];

function intersectAll() {
  for (const { node, observer } of [...observed]) observer.callback([{ isIntersecting: true, target: node }]);
}

test('shows the conversation in order with one header per stage, dimming entries after the cursor', () => {
  const root = document.querySelector('#transcript');
  const transcript = new Transcript(root, { onSelect() {}, loadDetail: async () => ({}), onClearAgent() {} });
  transcript.render({ events, agents, cursor: 4, selectedId: null, agentId: null });
  const rows = [...root.querySelectorAll('.tx-list > *')].map((node) => node.classList.contains('tx-stage') ? `# ${node.textContent}` : node.dataset.id);
  assert.deepEqual(rows, ['# Debate round 1', 'e1', 'e3', '# Debate round 2', 'e4', 'e5']);
  assert.equal(globalThis.scrolledTo, 'e4');
  assert.ok(root.querySelector('[data-id="e4"]').classList.contains('tx-at-cursor'));
  assert.deepEqual([...root.querySelectorAll('.tx-future')].map((node) => node.dataset.id), ['e5']);
  const kept = root.querySelector('[data-id="e1"]');
  transcript.render({ events, agents, cursor: 5, selectedId: 'e1', agentId: 'b' });
  assert.deepEqual([...root.querySelectorAll('.tx-entry')].map((node) => node.dataset.id), ['e3', 'e5']);
  transcript.render({ events, agents, cursor: 5, selectedId: 'e1', agentId: null });
  assert.equal(root.querySelector('[data-id="e1"]'), kept, 'entries are reused across renders');
  assert.ok(kept.classList.contains('tx-selected'));
});

test('message bodies load full content once; entries after the cursor wait until on screen', async () => {
  const root = document.createElement('section');
  const requests = [];
  const transcript = new Transcript(root, {
    onSelect() {},
    loadDetail: async (event) => { requests.push(event.id); return { data: { content: `Full ${event.id} with **bold**` } }; },
    onClearAgent() {},
  });
  observed.clear();
  transcript.render({ events, agents, cursor: 4, selectedId: null, agentId: null });
  const preview = root.querySelector('[data-id="e4"] .tx-body');
  assert.equal(preview.textContent, 'Third **preview**', 'raw text shows until the formatter is ready');
  await tick();
  assert.equal(preview.textContent.trim(), 'Third preview');
  const margins = new Map([...observed].map(({ node, observer }) => [node.dataset.id, observer.options.rootMargin]));
  assert.deepEqual(Object.fromEntries(margins), { e1: '600px 0px', e3: '600px 0px', e4: '600px 0px', e5: undefined });
  intersectAll();
  await tick();
  assert.deepEqual(requests.sort(), ['e1', 'e3', 'e4', 'e5']);
  const body = root.querySelector('[data-id="e4"] .tx-body');
  assert.equal(body.textContent.trim(), 'Full e4 with bold');
  assert.equal(body.querySelector('strong').textContent, 'bold');
  transcript.render({ events, agents, cursor: 3, selectedId: null, agentId: null });
  transcript.render({ events, agents, cursor: 5, selectedId: null, agentId: null });
  assert.equal(observed.size, 0);
  assert.deepEqual(requests.sort(), ['e1', 'e3', 'e4', 'e5']);
});

test('long histories window 150 entries before the cursor and 50 after, paging both ways', () => {
  const root = document.createElement('section');
  const transcript = new Transcript(root, { onSelect() {}, loadDetail: async () => ({}), onClearAgent() {} });
  const long = Array.from({ length: 400 }, (_, index) => message(index + 1, 'a', null, `Message ${index + 1}`));
  const ids = () => [...root.querySelectorAll('.tx-entry')].map((node) => node.dataset.id);
  const pageButton = (label) => [...root.querySelectorAll('.tx-page')].find((node) => node.textContent === label);
  transcript.render({ events: long, agents, cursor: 200, selectedId: null, agentId: null });
  assert.equal(ids().length, 200);
  assert.deepEqual([ids()[0], ids().at(-1)], ['e51', 'e250']);
  pageButton('Show earlier').click();
  assert.deepEqual([ids()[0], ids().at(-1)], ['e1', 'e250']);
  assert.equal(pageButton('Show earlier'), undefined);
  pageButton('Show later').click();
  assert.equal(ids().at(-1), 'e300');
});

test('the Show menu filters by event kind, "only this" picks one kind, and the choice persists', () => {
  const root = document.createElement('section');
  document.body.append(root);
  const selected = [];
  const mixed = [
    message(1, 'a', null, 'Hello'),
    { id: 't2', position: 2, kind: 'tool.started', agent_id: 'a', preview: 'search("x")' },
    { id: 'm3', position: 3, kind: 'memory.written', agent_id: 'b', preview: 'key: plan\\nvalue:\n be brief' },
    { id: 'c4', position: 4, kind: 'agent.updated', agent_id: 'b', intervention: true, preview: 'New prompt' },
    { id: 'o5', position: 5, kind: 'observation.recorded', label: 'note', preview: 'Seen' },
  ];
  const props = { events: mixed, agents, cursor: 5, selectedId: null, agentId: null };
  const ids = () => [...root.querySelectorAll('.tx-entry')].map((node) => node.dataset.id);
  const transcript = new Transcript(root, { onSelect: (event) => selected.push(event.id), loadDetail: async () => ({}), onClearAgent() {} });
  transcript.render(props);
  const show = root.querySelector('.tx-show-button');
  assert.deepEqual(ids(), ['e1', 'c4']);
  assert.equal(show.textContent, 'ShowMessages, Changes');

  show.click();
  assert.equal(show.getAttribute('aria-expanded'), 'true');
  root.querySelector('[aria-label="Show only Tools"]').click();
  assert.deepEqual(ids(), ['t2']);
  const memory = root.querySelector('input[aria-label="Memory"]');
  memory.checked = true;
  memory.dispatchEvent(new window.Event('change'));
  assert.deepEqual(ids(), ['t2', 'm3']);
  assert.equal(root.querySelector('.tx-show-summary').textContent, 'Tools, Memory');
  assert.equal(root.querySelector('[data-id="m3"] .tx-row-text').textContent, 'key: plan value: be brief');
  root.querySelector('[data-id="m3"]').click();
  assert.deepEqual(selected, ['m3']);

  root.querySelector('.tx-kinds').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(show.getAttribute('aria-expanded'), 'false');
  show.click();
  document.body.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
  assert.ok(root.querySelector('.tx-kinds').hidden);

  const reopened = document.createElement('section');
  new Transcript(reopened, { onSelect() {}, loadDetail: async () => ({}), onClearAgent() {} }).render(props);
  assert.deepEqual([...reopened.querySelectorAll('.tx-entry')].map((node) => node.dataset.id), ['t2', 'm3']);
  localStorage.clear();
  root.remove();
});
