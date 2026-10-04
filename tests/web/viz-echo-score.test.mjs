import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { shingles, messageLinks, scoreLink, findJumps, echoScore } from '../../src/swarm_lens/web/viz/echo-score.js';

const dom = new JSDOM('<!doctype html><body></body>');
Object.assign(globalThis, { window: dom.window, document: dom.window.document });
globalThis.ResizeObserver = class { observe() {} disconnect() {} };

const message = (position, agent, entity, sources, extra = {}) => ({ id: `e${position}`, position, kind: 'message.created',
  agent_id: agent, entity_id: entity, delivered_sources: sources, stage_label: `Round ${position}`, preview: '', ...extra });
const words = (from, count) => Array.from({ length: count }, (_, index) => `w${from + index}`).join(' ');
const scoreAll = (messages, texts) => {
  const sets = new Map(Object.entries(texts).map(([id, text]) => [id, shingles(text)]));
  return messageLinks(messages).links.map((link) => scoreLink(link, (event) => sets.get(event.id)));
};

test('shingles are lowercased five-word windows with punctuation and spacing ignored', () => {
  assert.deepEqual([...shingles('The  answer, is: 42!\nFinal answer.')],
    ['the answer is 42 final', 'answer is 42 final answer']);
  assert.deepEqual(shingles('The ANSWER is 42 final'), shingles('the answer... is 42\tfinal'));
  assert.equal(shingles('too short to count').size, 0);
});

test('echo is the share of shingles found in the delivered messages, with the top source and self-echo', () => {
  const messages = [message(1, 'a', 'm1', []), message(2, 'b', 'm2', []), message(3, 'a', 'm3', ['m2'])];
  const texts = { e1: words(0, 14), e2: words(100, 14), e3: `${words(100, 9)} ${words(0, 9)}` };
  const [, , third] = scoreAll(messages, texts);
  // 14 shingles: 5 inside each copied block, 4 spanning the seam.
  assert.equal(third.echo, 5 / 14);
  assert.equal(third.top.source.id, 'e2');
  assert.equal(third.selfEcho, 5 / 14);
});

test('a message waits for every text it depends on, and one that read nothing has no echo', () => {
  const messages = [message(1, 'a', 'm1', []), message(2, 'b', 'm2', ['m1'])];
  const [first, second] = scoreAll(messages, { e1: words(0, 10) });
  assert.equal(first.pending, false);
  assert.equal(first.echo, null);
  assert.equal(second.pending, true);
});

test('jumps are rises of 30 points over the same agent, largest first', () => {
  const score = (position, agent, echo) => ({ message: message(position, agent, `m${position}`, []), echo });
  const scores = [score(1, 'a', 0.1), score(2, 'b', 0.5), score(3, 'a', 0.4), score(4, 'b', 0.7), score(5, 'a', 0.95), score(6, 'b', 0.1)];
  const jumps = findJumps(scores);
  assert.deepEqual(jumps.map((jump) => [jump.score.message.id, jump.from]), [['e5', 0.4], ['e3', 0.1]]);
});

test('without delivery records, a message is compared with its author and the latest message of every other speaker', () => {
  const messages = [message(1, 'a', 'm1', null), message(2, 'b', 'm2', null), message(3, 'b', 'm3', null), message(4, 'a', 'm4', null)];
  const { delivered, links } = messageLinks(messages);
  assert.equal(delivered, false);
  assert.deepEqual(links[3].sources.map((event) => event.id).sort(), ['e1', 'e3']);
  assert.equal(links[3].previousOwn.id, 'e1');
});

test('the view loads texts with at most six requests in flight and says when it has no delivery records', async () => {
  const messages = Array.from({ length: 20 }, (_, index) => message(index + 1, index % 2 ? 'b' : 'a', `m${index}`, null));
  let inFlight = 0;
  let peak = 0;
  const loadDetail = async (event) => {
    peak = Math.max(peak, ++inFlight);
    await new Promise((resolve) => setTimeout(resolve, 2));
    inFlight--;
    return { data: { content: words(Number(event.id.slice(1)), 12) } };
  };
  const root = document.createElement('div');
  const toolbar = document.createElement('div');
  const view = echoScore.mount(root, { loadDetail, select() {}, selectAgent() {}, seek() {}, contextMenu() {} }, toolbar);
  view.update({ events: messages, cursor: 10, selectedId: 'e5', agents: { a: { name: 'Alpha' }, b: { name: 'Beta' } } });
  assert.match(root.textContent, /No delivery records/);
  assert.match(root.textContent, /Reading messages 0 of 20/);
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.ok(peak <= 6 && peak > 1, `peak in flight was ${peak}`);
  assert.doesNotMatch(root.textContent, /Reading messages/);
  assert.equal(root.querySelectorAll('.echo-point').length, 19);
  assert.equal(root.querySelectorAll('.echo-point.is-future').length, 10);
  assert.equal(root.querySelector('.echo-point.is-selected').dataset.tip.startsWith('Alpha · Round 5'), true);
  assert.equal(toolbar.querySelector('.echo-self-toggle').getAttribute('aria-pressed'), 'false');
  view.destroy();
  assert.equal(root.childNodes.length, 0);
  assert.equal(toolbar.childNodes.length, 0);
});

test('large runs read only a window of messages around the cursor', async () => {
  const messages = Array.from({ length: 2500 }, (_, index) => message(index + 1, `agent${index % 3}`, `m${index}`, index ? [`m${index - 1}`] : []));
  const requested = new Set();
  const loadDetail = async (event) => {
    requested.add(event.id);
    return { data: { content: words(0, 8) } };
  };
  const root = document.createElement('div');
  const view = echoScore.mount(root, { loadDetail, select() {}, selectAgent() {}, seek() {}, contextMenu() {} }, null);
  view.update({ events: messages, cursor: 1200, selectedId: null, agents: {} });
  assert.match(root.textContent, /Showing 400 of 2,500 messages around the cursor/);
  await new Promise((resolve) => setTimeout(resolve, 200));
  // The window plus the few earlier messages its first entries read or follow.
  assert.ok(requested.size <= 410, `requested ${requested.size} texts`);
  assert.ok(requested.has('e1200'));
  view.destroy();
});
