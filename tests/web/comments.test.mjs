import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'), { url: 'http://localhost/' });
for (const key of ['window', 'document', 'localStorage', 'HTMLElement', 'Element', 'Node', 'Event', 'FormData']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
}
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.requestAnimationFrame = (callback) => { callback(); return 1; };
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const { Comments, CommentStore, CommentIndex, readAuthor } = await import('../../src/swarm_lens/web/comments.js');
const { EventTimeline } = await import('../../src/swarm_lens/web/timeline.js');
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const comment = (id, eventId, position, extra = {}) => ({ id, branch_id: 'b1', event_id: eventId, position,
  author: 'Ada', text: `Note ${id}`, created_at: '2026-10-04T08:00:00Z', parent_id: null, resolved: false,
  updated_at: null, ...extra });
const saved = [
  comment('t1', 'e2', 2, { text: 'Injection lands here' }),
  comment('r1', 'e2', 2, { parent_id: 't1', author: 'Grace', text: 'Agreed' }),
  comment('t2', 'e2', 2, { author: 'Grace', resolved: true, text: 'Old question' }),
  comment('t3', 'e4', 4),
];

// A fake server: GET returns the branch's comments, mutations are recorded.
function server(t, byBranch = { b1: saved, b2: [] }) {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (path, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ method, path, body: options.body && JSON.parse(options.body) });
    const branch = /branches\/(\w+)\/comments/.exec(path)?.[1];
    const body = method === 'GET' ? { comments: byBranch[branch] } : { id: 'new' };
    return { ok: true, status: method === 'DELETE' ? 204 : 200, json: async () => body };
  });
  return requests;
}

test('the store caches per branch and refetches after a mutation or a branch change', async (t) => {
  const requests = server(t);
  let changes = 0;
  const store = new CommentStore(() => changes++);
  await store.load('b1');
  await store.load('b1');
  assert.equal(requests.length, 1);
  assert.deepEqual(store.index.threads.map((thread) => [thread.id, thread.replies.length]), [['t1', 1], ['t2', 0], ['t3', 0]]);
  assert.equal(store.index.openCount('e2'), 1);

  await store.add({ eventId: 'e4', author: 'Ada', text: 'More', parentId: 't3' });
  assert.deepEqual(requests.slice(1).map(({ method, path }) => `${method} ${path}`),
    ['POST /api/branches/b1/comments', 'GET /api/branches/b1/comments']);
  assert.deepEqual(requests[1].body, { event_id: 'e4', author: 'Ada', text: 'More', parent_id: 't3' });

  const switching = store.load('b2');
  assert.equal(store.index.threads.length, 0, 'the old branch\'s threads never show on the new branch');
  await switching;
  await store.load('b1');
  assert.equal(requests.filter(({ method, path }) => method === 'GET' && path === '/api/branches/b1/comments').length, 3);
  await store.remove('t3');
  assert.deepEqual(requests.at(-2), { method: 'DELETE', path: '/api/comments/t3', body: undefined });
  assert.equal(changes, 2, 'only changes made here notify; loading callers render themselves');
});

test('a thread shows its replies, resolve or reopen, and edit or delete only for the reader\'s own comments', async (t) => {
  const requests = server(t);
  localStorage.setItem('swarm-lens:comment-author', 'Ada');
  const comments = new Comments({ dataChanged() {}, viewChanged() {}, selectEvent() {} });
  await comments.load('b1');
  const section = comments.eventSection({ id: 'e2' });
  const [open, resolved] = section.querySelectorAll('.cm-thread');
  assert.deepEqual([...open.querySelectorAll('.cm-author')].map((node) => node.textContent), ['Ada', 'Grace']);
  assert.equal(open.querySelector('.cm-text').textContent, 'Injection lands here');
  const tools = (node) => [...node.querySelectorAll(':scope > .cm-head .cm-tool')].map((button) => button.textContent);
  const [top, reply] = open.querySelectorAll('.cm-comment');
  assert.deepEqual(tools(top), ['Resolve', 'Edit', 'Delete']);
  assert.deepEqual(tools(reply), []);
  assert.ok(open.querySelector('textarea[aria-label="Reply"]'));
  assert.ok(resolved.classList.contains('is-resolved'));
  assert.deepEqual(tools(resolved.querySelector('.cm-comment')), ['Reopen']);
  assert.equal(resolved.querySelector('textarea'), null, 'resolved threads take no replies');

  top.querySelector('.cm-tool').click();
  resolved.querySelector('.cm-tool').click();
  await settle();
  assert.deepEqual(requests.filter((request) => request.method === 'PATCH').map(({ path, body }) => [path, body]),
    [['/api/comments/t1', { resolved: true }], ['/api/comments/t2', { resolved: false }]]);
  assert.equal(comments.eventSection({ id: 'e9' }), null);
  const overview = comments.overviewSection([{ id: 'e2', position: 2, kind: 'message.created', agent_id: 'a' }], { a: { name: 'Debater 0' } });
  assert.equal(overview.querySelector('.section-title').textContent, 'Comments (2 open)');
  assert.equal(overview.querySelector('.cm-anchor').textContent, 'Debater 0 · #2');
});

test('the first comment asks for a name once, and the name persists in this browser', async (t) => {
  const requests = server(t, { b1: [] });
  localStorage.clear();
  let views = 0;
  const comments = new Comments({ dataChanged() {}, viewChanged: () => views++, selectEvent() {} });
  await comments.load('b1');
  comments.start('e2');
  const form = comments.eventSection({ id: 'e2' }).querySelector('form');
  form.querySelector('.cm-name').value = '  Lin  ';
  form.querySelector('textarea').value = 'Why does round 3 flip?';
  form.requestSubmit();
  await settle();
  assert.deepEqual(requests.find((request) => request.method === 'POST').body,
    { event_id: 'e2', author: 'Lin', text: 'Why does round 3 flip?', parent_id: null });
  assert.equal(readAuthor(), 'Lin');
  assert.equal(comments.composing, null);
  assert.equal(views, 1);
  const next = new Comments({ dataChanged() {}, viewChanged() {}, selectEvent() {} });
  next.start('e2');
  assert.equal(next.eventSection({ id: 'e2' }).querySelector('.cm-name'), null);

  t.mock.method(dom.window.Storage.prototype, 'getItem', () => { throw new Error('blocked'); });
  assert.equal(readAuthor(), '', 'blocked storage means asking again, not failing');
});

test('a draft and its focus survive an inspector re-render', async (t) => {
  server(t);
  const comments = new Comments({ dataChanged() {}, viewChanged() {}, selectEvent() {} });
  await comments.load('b1');
  const root = document.querySelector('#inspector');
  root.replaceChildren(comments.eventSection({ id: 'e2' }));
  const reply = root.querySelector('textarea[aria-label="Reply"]');
  reply.focus();
  reply.value = 'Half a thought';
  reply.dispatchEvent(new window.Event('input'));
  root.replaceChildren(comments.eventSection({ id: 'e2' }));
  comments.restoreFocus(root);
  const again = root.querySelector('textarea[aria-label="Reply"]');
  assert.notEqual(again, reply);
  assert.equal(again.value, 'Half a thought');
  assert.equal(document.activeElement, again);
});

test('lanes draw one note per commented event above the lanes, hiding resolved threads unless asked', () => {
  localStorage.clear();
  const at = (seconds) => new Date(Date.parse('2026-10-04T08:00:00Z') + seconds * 1000).toISOString();
  const events = [1, 2, 3, 4].map((position) => ({ id: `e${position}`, position, at: at(position * 10),
    kind: 'message.created', agent_id: 'a', channel_id: 'chat', label: 'Message', preview: `Turn ${position}` }));
  const root = document.querySelector('#timeline');
  const timeline = new EventTimeline(root, { onSeek() {}, onSelect: (event) => (selected = event.id), onContext() {}, onAgent() {}, onPlay() {} });
  let selected = null;
  const viewport = root.querySelector('.tl-viewport');
  Object.defineProperty(viewport, 'clientWidth', { value: 1100 });
  timeline.setData(events, { id: 'b1', head: 4 });
  timeline.setCursor(3);
  const laneTop = timeline.laneY('a');
  timeline.setComments(new CommentIndex(saved).threads);
  const notes = () => [...root.querySelectorAll('.tl-note')];
  assert.deepEqual(notes().map((node) => [node.dataset.eventId, node.style.left, node.textContent]),
    [['e2', `${timeline.x(events[1])}px`, ''], ['e4', `${timeline.x(events[3])}px`, '']]);
  assert.ok(notes()[1].classList.contains('is-future'));
  assert.equal(timeline.laneY('a'), laneTop + 18, 'the note row pushes the lanes down');
  assert.equal(root.style.getPropertyValue('--tl-ruler-h'), '58px');

  const showResolved = [...root.querySelectorAll('.tl-check')].find((row) => row.textContent === 'Show resolved');
  assert.equal(showResolved.hidden, false);
  showResolved.firstChild.checked = true;
  showResolved.firstChild.dispatchEvent(new window.Event('change'));
  assert.equal(notes()[0].textContent, '2', 'two threads on one event show a count');
  notes()[0].click();
  assert.equal(selected, 'e2');

  showResolved.firstChild.checked = false;
  showResolved.firstChild.dispatchEvent(new window.Event('change'));
  timeline.setComments([]);
  assert.equal(notes().length, 0);
  assert.equal(timeline.laneY('a'), laneTop, 'without notes the ruler keeps its height');
});
