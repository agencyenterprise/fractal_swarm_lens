# Build an application with Swarm Lens

The framework does not recognize arbitrary datasets. Your application defines what its source records mean, produces facts, chooses plugins and execution services, and mounts whichever UI or API it needs.

```python
from swarm_lens import Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.adapters.git import GitVersions

class MySource:
    def facts(self):
        at = "2026-01-01T12:00:00+00:00"
        yield Fact("environment.updated", {"task": "Investigate", "goal": "Explain the evidence"}, at)
        yield Fact("agent.added", {"id": "researcher", "name": "Researcher", "model": "my-model"}, at)
        yield Fact("channel.created", {"id": "team", "name": "Team", "members": ["researcher"]}, at)
        yield Fact("message.created", {
            "id": "m1", "channel_id": "team", "sender_id": "researcher", "content": "First observation",
        }, at, source={"table": "my_messages", "row_id": "42"})

framework = Framework(SQLiteHistory("my-data/history.sqlite"), versions=GitVersions("my-data/history.git"))
recorded = framework.create_run("My investigation")
framework.ingest(recorded.id, MySource())
experiment = framework.fork(recorded.id, 4, "Alternative prompt")
framework.intervene(experiment.id, "agent.updated", {
    "id": "researcher", "system_prompt": "Check every claim against source evidence.",
}, expected_head=4)
state = framework.state(experiment.id)
commit = framework.checkpoint(experiment.id)
```

Inputs use timezone-aware ISO timestamps. Emit creation facts before facts that reference those identities. Use globally unique event IDs if supplying them explicitly; omitted IDs are generated. Domain entity IDs can recur across runs. Application-specific source identifiers belong in provenance. The same memory ID denotes revisions of one slot; use different IDs for independent memories.

The maintained AI Village research example derives stable event IDs from native source identities and records a source checksum. Reimporting the same bundle reuses its matching recording. These are explicit application choices, not generic dataset interpretation.

## Plugins

Analyses and interventions are plugins. A plugin reads a branch through a read-only view and returns metrics, annotations, a report, or the facts for a new fork. The application validates and stores them, and the explorer shows them without plugin-specific frontend code. See [writing a plugin](plugins.md) for the contract, registration, the HTTP API and testing.

## Runtime and presentation

Implement the `Runtime` protocol's `capabilities()` and `continue_from(state, steps)` to yield new facts from actual execution. Register it with `Framework(..., runtime=runtime)`, then call `framework.continue_run(branch_id, steps)`. Provisioning machines, restoring external tools, translating prompt/steering configuration, and recording model outputs belong to that runtime. The framework cannot resume the original AI Village machines from this observational export.

Use `swarm_lens.web.api.create_app(framework, artifacts=...)` for the supplied browser explorer, or call the same application services from a different UI. See the small composition root in `swarm_lens/cli.py`. Core behavior is independent of the browser and FastAPI.

## Write a web plugin

A web plugin adds API routes and, optionally, browser UI to the explorer without editing the core or the explorer's code. It is trusted code: its Python runs in the server process and its module runs with the page's full privileges. Install only plugins you would merge.

A plugin is a `WebExtension(id, router, manifest, assets=None)` from `swarm_lens.web.extensions`:

- `id` is lowercase letters, digits and hyphens. Every route of `router` must start with `/api/plugins/{id}/`. Duplicate IDs or routes fail startup. A router `lifespan` runs with the application's, for startup recovery.
- `manifest()` returns plugin-owned fields such as `title`, `version` and `modes`. `/api/workspace` lists it under `capabilities.web_plugins`, with `id`, `api_prefix` and `ui` added by the server.
- `assets` is a directory of public browser files, served at `/assets/plugins/{id}/`. It must contain `index.js`, which the explorer imports at startup (`ui.module` in the manifest). Keep Python files out of this directory.

`index.js` exports `install(host, manifest)`. It runs once, after the workspace loads and before a saved link reopens a plugin view. If it throws or fails to load, the explorer reports the plugin by name, removes the views, actions and visualizations it registered, and continues without it. The host offers:

| Method | Purpose |
| --- | --- |
| `context()` | `{ workspace, run, branch, cursor }` for the current selection; `branch` is null before a run opens |
| `registerView({ id, title, tip, onShow(params), onHide() })` | Adds a workspace tab and returns its panel element |
| `openView(id, params)` | Shows a workspace view; `params` ride in the URL so links reopen it |
| `setViewParams(id, params)` | Updates the URL params of the view that is showing |
| `openBranchView(branchId, cursor, viewId, params)` | Opens a branch at a cursor, then a view |
| `openTimeline(branchId, cursor, eventId?)` | Opens the timeline at a cursor, selecting the event if given |
| `loadTimeline(branchId)`, `loadDetail(branchId, event)` | Cached event summaries and full event records |
| `addAction({ label, tip, onClick })` | Adds an entry under the plugin's title in the `⋯` menu |
| `registerVisualization(config)` | Adds a view above the transcript; see the README's development section |

Shared browser helpers are imported by a stable name that the page maps to the explorer's own modules: `swarm-lens/ui.js` (elements, `api`, `post`, toasts, tooltips), `swarm-lens/dialog.js`, `swarm-lens/markdown.js` and `swarm-lens/workspace.js`. Node tests resolve the same names through `package.json` exports. Call your own routes with `api("/plugins/{id}/...")`.

```python
from pathlib import Path
from fastapi import APIRouter
from swarm_lens.web.extensions import WebExtension

def notes_extension(store):
    router = APIRouter(prefix="/api/plugins/notes")

    @router.get("/items")
    def items(branch_id: str):
        return {"items": store.items(branch_id)}

    return WebExtension("notes", router, lambda: {"title": "Notes", "version": "1"},
                        assets=Path(__file__).parent / "static")
```

```js
// static/index.js
import { api, el } from "swarm-lens/ui.js";

export function install(host) {
  const panel = host.registerView({ id: "notes", title: "Notes", onShow: async () => {
    const { branch } = host.context();
    if (!branch) return;
    const { items } = await api(`/plugins/notes/items?branch_id=${encodeURIComponent(branch.id)}`);
    panel.replaceChildren(...items.map((item) => el("p", "", item)));
  } });
  host.addAction({ label: "Open notes", onClick: () => host.openView("notes") });
}
```

Pass the extension to `create_app(framework, artifacts, extensions=(notes_extension(store),))` in your own composition root. With the bundled explorer, register a factory instead: it receives `PluginServices(framework, artifacts, data, plugins)` and returns the extension, or a list that also holds [method plugins](plugins.md).

```sh
swarm-lens --data data --plugin my_package.notes:create   # repeatable; bundled and installed plugins are always included
```

MAST (`src/swarm_lens/web/plugins/mast/`) is a complete example that follows exactly this contract. Plugin styles are the plugin's own concern; MAST's stylesheet is part of the shared build only because it predates this contract.

## Observe a history with a method

Use `ObservabilityPlugin(method_id, version, factory, adapter)` from `swarm_lens.observability`. The factory receives analysis configuration and returns a fresh method. The application adapter consumes the selected `Event` history and yields method-specific observations. Its `id`, `version`, and `describe()` document mapping/encoding provenance. It is an `Analyzer`: `PluginService.analyze` persists the resolved method/adapter metadata and per-turn results as the record's `output.report`, with the branch history digest.

The [CASPIAN branch regression fixture](../tests/support/caspian_branch.py) runs a complete SQLite application, historical analysis, intervention, and nested fork without credentials. Follow the [method input contract](observability/caspian/README.md) to supply genuine downstream observations; a branch edit itself does not execute the runtime or generate model-internal evidence.
