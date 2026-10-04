import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { renderMessageHTML } from '../../frontend/message-format.js';

const render = text => JSDOM.fragment(renderMessageHTML(text));

test('formats numbered reasoning, emphasis, tables, and equations inside list paragraphs', () => {
  const body = render(String.raw`Let's break down the problem step by step.

1. **Initial number of cards**: Elaine starts with 20 Pokemon cards.

2. **First month**: She collects three times the initial number of cards.
   \[
   \text{Cards collected in the first month} = 3 \times 20 = 60
   \]
   After the first month, her total number of cards is:
   \[
   20 + 60 = 80
   \]

3. **Second month**: She collects *20 fewer* cards.

| Month | Cards |
| --- | --- |
| First | 80 |`);
  assert.equal(body.querySelectorAll('ol > li').length, 3);
  assert.equal(body.querySelectorAll('strong').length, 3);
  assert.equal(body.querySelector('em').textContent, '20 fewer');
  assert.equal(body.querySelectorAll('math[display="block"]').length, 2);
  assert.equal(body.querySelector('table tbody td').textContent, 'First');
});

test('supports both math delimiter styles without interpreting math inside code', () => {
  const body = render(String.raw`Inline \(x^2\) and $y^2$.

\[
\frac{1}{2}
\]

$$z = 3$$

Keep \(missing delimiter as text.

` + '`\\[literal\\]`\n\n```text\n$literal$\n\\[literal\\]\n```');
  assert.equal(body.querySelectorAll('math').length, 4);
  assert.equal(body.querySelectorAll('code math').length, 0);
  assert.match(body.querySelector('pre').textContent, /\$literal\$/);
  assert.match(body.textContent, /missing delimiter as text/);
});

test('recorded HTML, unsafe links and TeX cannot execute or load external resources', () => {
  const body = render(String.raw`<img src=x onerror=alert(1)><script>alert(1)</script>

![Screenshot](https://example.com/tracking.png)

[bad](javascript:alert(1)) [relative](/api/private) [source](https://example.com)

\(\href{javascript:alert(1)}{click}\)

\(\includegraphics{https://example.com/tracking.png}\)`);
  assert.equal(body.querySelector('script, img, iframe, object, style'), null);
  assert.equal(body.querySelectorAll('a').length, 1);
  assert.equal(body.querySelector('a').getAttribute('rel'), 'noopener noreferrer');
  assert.equal(body.querySelector('a').getAttribute('href'), 'https://example.com');
  assert.match(body.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(body.textContent, /Image: Screenshot/);
});

test('invalid math stays visible instead of breaking the message reader', () => {
  const body = render(String.raw`Before \(\notARealCommand{x}\) after.`);
  assert.match(body.textContent, /Before/);
  assert.match(body.textContent, /notARealCommand/);
  assert.match(body.textContent, /after/);
});
