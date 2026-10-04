import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body><button id="play" data-tip="Play (Space)" aria-describedby="hint">▶</button><span id="hint"></span></body>',
  { url: 'http://localhost/', pretendToBeVisual: true });
for (const key of ['window', 'document', 'innerWidth', 'innerHeight'])
  globalThis[key] = key === 'window' ? dom.window : dom.window[key];
const { installTooltips, TIP_DELAY } = await import('../../src/swarm_lens/web/ui.js');
installTooltips(document);

const button = document.querySelector('#play');
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tooltip = () => document.querySelector('#tooltip');
const shown = () => Boolean(tooltip() && !tooltip().hidden);
const pointer = (type, target, relatedTarget = null) =>
  target.dispatchEvent(new window.MouseEvent(type, { bubbles: true, relatedTarget }));
const escape = () => document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

test('hover shows the tooltip only after the delay and wires aria-describedby', async () => {
  pointer('pointerover', button);
  await wait(TIP_DELAY / 2);
  assert.equal(shown(), false);
  await wait(TIP_DELAY);
  assert.equal(shown(), true);
  assert.equal(tooltip().textContent, 'Play (Space)');
  assert.equal(tooltip().getAttribute('role'), 'tooltip');
  assert.equal(button.getAttribute('aria-describedby'), 'hint tooltip');
  pointer('pointerout', button, document.body);
  assert.equal(shown(), false);
  assert.equal(button.getAttribute('aria-describedby'), 'hint');
});

test('keyboard focus shows it at once and Escape hides it', () => {
  button.focus();
  button.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }));
  assert.equal(shown(), true);
  escape();
  assert.equal(shown(), false);
  assert.equal(button.getAttribute('aria-describedby'), 'hint');
});

test('leaving before the delay cancels the tooltip', async () => {
  pointer('pointerover', button);
  pointer('pointerout', button, document.body);
  await wait(TIP_DELAY + 50);
  assert.equal(shown(), false);
});
