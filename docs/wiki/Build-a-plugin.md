# Build a plugin

A web plugin contributes a namespaced API and optionally a frontend module without adding imports to the explorer. Python and JavaScript plugins are trusted application code, not sandboxed extensions.

## Minimal module

Create `my_plugin/__init__.py`:

```python
from pathlib import Path
from fastapi import APIRouter
from swarm_lens.web.extensions import WebExtension

def create(services):
    router = APIRouter(prefix='/api/plugins/notes')

    @router.get('/summary')
    def summary():
        return {'message': 'Ready to inspect a trace'}

    return WebExtension(
        'notes', router,
        lambda: {'title': 'Research notes', 'version': '1'},
        assets=Path(__file__).parent / 'static',
    )
```

Create `my_plugin/static/index.js`:

```js
import { api, el } from 'swarm-lens/ui.js';

export function install(host, manifest) {
  const panel = host.registerView({
    id: 'notes',
    title: 'Notes',
    onShow: async () => {
      const result = await api('/plugins/notes/summary');
      panel.replaceChildren(el('p', '', result.message));
    },
  });
  host.addAction({
    label: 'Open research notes',
    onClick: () => host.openView('notes'),
  });
}
```

From the directory containing `my_plugin`, with Swarm Lens installed:

```sh
swarm-lens --data data --plugin my_plugin:create
```

## Contract

The factory receives `PluginServices(framework, artifacts, data)`. Routes must stay under `/api/plugins/{id}/`; IDs must be unique lowercase letters, digits, and hyphens. The assets directory must contain `index.js`. The server advertises its URL; the browser loads modules before restoring a saved route. Failed installations remove registered UI elements and report the plugin failure.

The host offers view and visualization registration, actions, current context, timeline/detail loading, and navigation to a branch/cursor. MAST is the bundled reference implementation. [Full API contract](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/integration.md#write-a-web-plugin).

A core analysis `Plugin` and a `WebExtension` are different interfaces. `--plugin` registers the web extension; your factory wires any analysis service it requires. Package browser assets with your distribution, and keep credentials and Python files out of its public assets directory.
