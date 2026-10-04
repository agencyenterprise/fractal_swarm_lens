# Build a plugin

A web plugin adds its own API routes, and optionally a frontend module, to the explorer without you touching the explorer's code. Plugins run as ordinary Python and JavaScript with full access to the app. There's no sandbox, so only install ones you trust.

## A minimal plugin

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

And `my_plugin/static/index.js`:

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

Then, from the directory that contains `my_plugin`, with Swarm Lens installed:

```sh
swarm-lens --data data --plugin my_plugin:create
```

## The rules

Your factory gets a `PluginServices(framework, artifacts, data)`. A few constraints:

- Routes live under `/api/plugins/{id}/`.
- IDs must be unique and use only lowercase letters, digits, and hyphens.
- The assets directory must contain an `index.js`.

The server tells the browser where your module is, and the browser loads all plugins before it restores the saved route. If a plugin fails to install, any UI it had already registered is removed and the failure is reported.

From `host`, you can register views and visualizations, add actions, read the current context, load timeline and detail data, and jump to a branch and cursor. The bundled MAST plugin is a full working example. The complete API is in the [integration guide](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/integration.md#write-a-web-plugin).

One distinction that trips people up: an analysis `Plugin` and a `WebExtension` are separate interfaces. `--plugin` registers the web extension, and it's up to your factory to set up any analysis service behind it. When you package a plugin, ship the browser assets with it, and keep credentials and Python source out of the public assets directory.
