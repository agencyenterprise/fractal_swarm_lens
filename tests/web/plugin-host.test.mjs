import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { installWebPlugins } from '../../src/swarm_lens/web/plugin-host.js';

const manifest = (id, module = `/assets/plugins/${id}/index.js`) => ({ id, title: `${id} title`, ui: module && { module } });

test('plugins install in manifest order, and a broken plugin is removed without stopping the others', async () => {
  const calls = [], reports = [], disposed = [];
  const modules = {
    '/assets/plugins/slow/index.js': new Promise(resolve => setTimeout(() => resolve({ install: (host, m) => calls.push([host.owner, m.id]) }), 10)),
    '/assets/plugins/missing/index.js': Promise.reject(new Error('404')),
    '/assets/plugins/no-install/index.js': Promise.resolve({}),
    '/assets/plugins/half/index.js': Promise.resolve({ install: async host => { calls.push([host.owner, 'registered']); throw null; } }),
    '/assets/plugins/fast/index.js': Promise.resolve({ install: async (host, m) => calls.push([host.owner, m.id]) }),
  };
  const installed = await installWebPlugins(
    [manifest('slow'), manifest('missing'), manifest('routes-only', null), manifest('no-install'), manifest('half'), manifest('fast')],
    { hostFor: m => ({ owner: m.title, dispose: () => disposed.push(m.id) }), report: error => reports.push(error.message),
      load: url => modules[url] },
  );
  assert.deepEqual(installed, ['slow', 'fast']);
  assert.deepEqual(calls, [['slow title', 'slow'], ['half title', 'registered'], ['fast title', 'fast']]);
  assert.deepEqual(disposed, ['missing', 'no-install', 'half']);
  assert.deepEqual(reports, [
    'Plugin missing title failed to load: 404',
    'Plugin no-install title failed to load: /assets/plugins/no-install/index.js does not export install(host, manifest)',
    'Plugin half title failed to load: null',
  ]);
});

test('the bundled MAST plugin installs through the host contract alone', async () => {
  const page = await readFile(new URL('../../src/swarm_lens/web/index.html', import.meta.url), 'utf8');
  const { window } = new JSDOM(page, { url: 'http://localhost/' });
  for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event', 'navigator', 'MutationObserver'])
    Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? window : window[key] });
  const views = [], actions = [];
  const host = {
    context: () => ({ workspace: null, run: null, branch: null, cursor: 0 }),
    registerView: config => { views.push(config.id); return window.document.createElement('section'); },
    addAction: action => actions.push(action.label),
    dispose: () => assert.fail('MAST installed cleanly'),
  };
  const installed = await installWebPlugins([manifest('mast')], {
    hostFor: () => host, report: error => { throw error; },
    load: () => import('../../src/swarm_lens/web/plugins/mast/static/index.js'),
  });
  assert.deepEqual(installed, ['mast']);
  assert.deepEqual(views, ['mast']);
  assert.deepEqual(actions, ['Analyze with MAST…']);
});
