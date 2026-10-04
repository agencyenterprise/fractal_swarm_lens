import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM(readFileSync(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8'));
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event'])
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? dom.window : dom.window[key] });
const { renderParamsForm } = await import('../../src/swarm_lens/web/params-form.js');

// The shape pydantic's model_json_schema() emits, including a $ref enum and Optional fields.
const schema = {
  type: 'object',
  $defs: { Mode: { enum: ['fast', 'thorough'], title: 'Mode', type: 'string' } },
  properties: {
    question: { type: 'string', title: 'Question', description: 'What to look for', maxLength: 200 },
    notes: { type: 'string', title: 'Notes', format: 'textarea', default: '' },
    window: { type: 'integer', title: 'Window', default: 5, minimum: 1, maximum: 50 },
    threshold: { type: 'number', title: 'Threshold', default: 0.5 },
    strict: { type: 'boolean', title: 'Strict', default: false },
    mode: { $ref: '#/$defs/Mode', default: 'fast' },
    limit: { anyOf: [{ type: 'integer' }, { type: 'null' }], title: 'Limit', default: null },
    flagged: { anyOf: [{ type: 'boolean' }, { type: 'null' }], title: 'Flagged', default: null },
    focus: { anyOf: [{ $ref: '#/$defs/Mode' }, { type: 'null' }], default: null },
  },
  required: ['question'],
};
const control = (form, name) => form.element.querySelector(`#field-param-${name}`);
const set = (form, name, value) => { control(form, name).value = value; };

test('each schema type renders its control with defaults, labels and help', () => {
  const form = renderParamsForm(schema);
  assert.equal(control(form, 'question').tagName, 'INPUT');
  assert.equal(control(form, 'question').required, true);
  assert.equal(control(form, 'question').maxLength, 200);
  assert.match(form.element.textContent, /What to look for/);
  assert.equal(control(form, 'notes').tagName, 'TEXTAREA');
  assert.deepEqual([control(form, 'window').type, control(form, 'window').min, control(form, 'window').max, control(form, 'window').step],
    ['number', '1', '50', '1']);
  assert.equal(control(form, 'threshold').step, 'any');
  assert.equal(control(form, 'strict').type, 'checkbox');
  assert.equal(control(form, 'mode').selectedOptions[0].textContent, 'fast');
  assert.deepEqual([...control(form, 'focus').options].map(option => option.textContent), ['—', 'fast', 'thorough']);
  assert.deepEqual([...control(form, 'flagged').options].map(option => option.textContent), ['—', 'Yes', 'No']);
  assert.equal(form.element.querySelector('label[for="field-param-mode"]').textContent, 'Mode');
});

test('values are typed, empty optional fields are omitted, and invalid input is reported', () => {
  const form = renderParamsForm(schema);
  assert.deepEqual(form.validate(), ['Question is required.']);
  set(form, 'question', 'Who agrees first?');
  set(form, 'window', '7');
  control(form, 'strict').checked = true;
  set(form, 'mode', '1');
  set(form, 'flagged', '1');
  assert.deepEqual(form.validate(), []);
  assert.deepEqual(form.values(), { question: 'Who agrees first?', window: 7, threshold: 0.5, strict: true,
    mode: 'thorough', flagged: false });
  set(form, 'window', '2.5');
  set(form, 'limit', '0');
  assert.deepEqual(form.validate(), ['Window must be a whole number.']);
  set(form, 'window', '99');
  assert.deepEqual(form.validate(), ['Window must be at most 50.']);
});

test('a schema without properties renders an empty form', () => {
  const form = renderParamsForm({ type: 'object', properties: {} });
  assert.equal(form.element.children.length, 0);
  assert.deepEqual(form.values(), {});
});
