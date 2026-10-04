import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true, url: 'http://localhost/' });
const { window } = dom;
for (const key of ['window', 'document', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'Element', 'Node', 'Event', 'CustomEvent', 'KeyboardEvent', 'MutationObserver', 'NodeFilter', 'DOMRect', 'FormData', 'navigator']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? window : window[key] });
}
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
window.ResizeObserver = globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
window.HTMLElement.prototype.scrollIntoView = function () {};
window.HTMLElement.prototype.scrollTo = function () {};
globalThis.CSS = window.CSS = { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, character => '\\'+character) };
const { Picker, disposePickers } = await import('../../frontend/components.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 35));
const press = (target, key) => target.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

function fixture(t, props) {
  const form = document.createElement('form');
  document.body.append(form);
  const picker = new Picker(props);
  form.append(picker.root);
  picker.mount();
  t.after(() => { picker.destroy(); form.remove(); });
  return { picker, form };
}

test('select supports keyboard choice and submits the selected value through FormData', async t => {
  const { picker, form } = fixture(t, { label: 'Completeness', name: 'completeness', value: 'unknown', options: [
    { value: 'unknown', label: 'Unknown' }, { value: 'complete', label: 'Complete trace' }, { value: 'partial', label: 'Partial trace' },
  ] });
  const changes = [];
  picker.onchange = event => changes.push(event.target.value);
  assert.equal(new FormData(form).get('completeness'), 'unknown');
  picker.focus();
  press(picker.trigger, 'ArrowDown');
  await settle();
  assert.equal(picker.trigger.getAttribute('aria-expanded'), 'true');
  press(picker.list, 'End');
  await settle();
  press(picker.list, 'Enter');
  await settle();
  assert.equal(picker.value, 'partial');
  assert.equal(new FormData(form).get('completeness'), 'partial');
  assert.deepEqual(changes, ['partial']);
  assert.equal(picker.positioner.hidden, true);
  picker.value = 'complete';
  await settle();
  assert.equal(new FormData(form).get('completeness'), 'complete');
  assert.deepEqual(changes, ['partial'], 'programmatic updates must not navigate or submit');
});

test('search filters metadata, exposes an empty state, and escapes untrusted labels', async t => {
  const { picker } = fixture(t, { label: 'Conversations', searchable: true, options: [
    { value: 'a', label: 'CrewAI arithmetic', description: '21:12 UTC', detail: '3 branches' },
    { value: 'b', label: 'CrewAI arithmetic', description: '15:38 UTC', detail: '1 branch' },
    { value: 'c', label: '<img src=x onerror=alert(1)>', description: 'Untrusted trace' },
  ] });
  picker.focus();
  picker.input.value = '';
  picker.input.dispatchEvent(new Event('input', { bubbles: true }));
  await settle();
  assert.equal(picker.input.value, '', 'clearing search must not restore the selected label');
  picker.input.value = '15:38';
  picker.input.dispatchEvent(new Event('input', { bubbles: true }));
  await settle();
  assert.equal(picker.rows.length, 1);
  assert.equal(picker.rows[0].item.value, 'b');
  press(picker.input, 'Enter');
  await settle();
  assert.equal(picker.value, 'b');
  picker.input.value = 'no matches';
  picker.input.dispatchEvent(new Event('input', { bubbles: true }));
  await settle();
  assert.equal(picker.rows.length, 0);
  assert.equal(picker.empty.hidden, false);
  press(picker.input, 'Escape');
  await settle();
  assert.equal(picker.value, 'b', 'closing search must preserve the selected conversation');
  assert.equal(picker.input.value, 'CrewAI arithmetic', 'closing a filtered search restores the selected label');
  picker.setCollection(picker.options);
  assert.equal(picker.list.querySelector('img'), null);
  assert.ok(picker.list.textContent.includes('<img src=x'));
});

test('pointer selection keeps a dialog menu within its dialog', async t => {
  const dialog = document.createElement('dialog');
  const form = document.createElement('form');
  dialog.append(form);
  document.body.append(dialog);
  dialog.setAttribute('open', '');
  const picker = new Picker({ label: 'Checkpoint', name: 'cursor', options: [
    { value: '5', label: 'Before task 1' }, { value: '16', label: 'Before task 2' },
  ] });
  form.append(picker.root);
  picker.mount();
  t.after(() => { picker.destroy(); dialog.remove(); });
  picker.trigger.click();
  await settle();
  assert.equal(picker.positioner.parentElement, dialog);
  assert.equal(picker.trigger.getAttribute('aria-expanded'), 'true');
  picker.rows[1].row.click();
  await settle();
  assert.equal(new FormData(form).get('cursor'), '16');
  assert.equal(picker.positioner.hidden, true);
});

test('discovered conversations preserve selection and dialog cleanup removes portals', async t => {
  const { picker, form } = fixture(t, { label: 'Choice', options: [{ value: 'old', label: 'Existing' }] });
  let changes = 0;
  picker.onchange = () => changes++;
  picker.setOptions([{ value: 'new', label: 'New capture' }, { value: 'old', label: 'Existing' }]);
  await settle();
  assert.equal(picker.value, 'old');
  assert.equal(changes, 0);
  assert.ok(document.body.contains(picker.positioner));
  disposePickers(form);
  assert.equal(document.body.contains(picker.positioner), false);
});

test('a star toggles a favorite without selecting it, and the filter shows favorites only', async t => {
  const starred = new Set();
  const favorites = { has: value => starred.has(value), toggle: value => (starred.delete(value) || starred.add(value)) };
  const { picker } = fixture(t, { label: 'Run', heading: 'Runs', searchable: true, value: 'a', favorites, options: [
    { value: 'a', label: 'First run' }, { value: 'b', label: 'Second run' },
  ] });
  const changes = [];
  picker.onchange = event => changes.push(event.target.value);
  const star = picker.rows[1].row.querySelector('.picker-star');
  star.click();
  await settle();
  assert.equal(picker.value, 'a', 'starring must not select the option');
  assert.deepEqual(changes, []);
  assert.equal(star.getAttribute('aria-pressed'), 'true');
  picker.heading.querySelector('.picker-favorites-filter').click();
  await settle();
  assert.deepEqual(picker.rows.map(row => row.item.value), ['b']);
  assert.match(picker.heading.textContent, /Runs · 1 of 2/);
});
