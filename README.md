# Swarm Lens

A self-hosted framework for observing, replaying, branching, and intervening in agent systems. The included AI Village application demonstrates how a developer maps one source system into the framework's primitives. Dataset interpretation belongs to that application.

## Project priorities

Swarm Lens supports saved traces and live collection over the same event history. [MAST saved-trace analysis](docs/observability/mast/README.md) provides an on-demand LLM judge, a composed plugin API, and UI controls for analyzing traces; importing a raw transcript is a core action. [CrewAI live collection and branch execution](examples/crewai/README.md) use a pinned adapter, durable ingestion, WebSocket updates, registered application runtimes, and a continuation adapter for imported conversations. ACIArena continuations preserve the debater/aggregator round structure; other traces use an explicit round-robin schedule and registered tools. Docker deployment remains a priority. See the [implementation priorities and acceptance criteria](docs/architecture.md#implementation-priorities).

CASPIAN stays as an optional experimental plugin. We have not validated that it reliably detects cascade effects; executable code and equation-level tests do not establish detection accuracy. Observability and trace collection must remain useful independently of CASPIAN.

## Run the included explorer

Python 3.11+ and Git are required. The local demo workspace stores selected AI Village data, SQLite history, model-response artifacts, and Git checkpoints under `data/`. These files and all environment files are excluded from Git.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,village,dev]'
swarm-lens --data data --port 8765
```

On a fresh clone, fetch and import the example before starting the server. The fetch command prompts for your Hugging Face token without saving it; your account needs access to the dataset.

```sh
python -m examples.ai_village.fetch --output data/source
python -m examples.ai_village.app --source data/source --data data
```

Open http://127.0.0.1:8765. The server binds to loopback by default. In an environment that already has FastAPI and Uvicorn, `PYTHONPATH=src python3 -m swarm_lens.cli --data data --port 8765` also works.

The explorer has three views. **Timeline** shows one lane per agent and channel, grouped into stages such as debate rounds. Risk events (an installed injection or malicious agent) carry a red flag, user changes an orange one, and a fork line marks where the current branch left its parent. Drag the playhead or the ruler, or use the arrow keys, to move through history; **View** chooses which event types appear. Below the timeline, the transcript renders each message as Markdown; events after the cursor stay visible but dimmed. The inspector on the right shows the selected event, agent, or run overview. **Compare** reads two branches side by side, final outcome first, with the first difference marked; paired runs such as "Without injection" and "With injection" compare directly. **Reports** holds saved MAST and timeline analyses.

Select an event, then **Fork here** (or press `F`) to create a branch at that point. The inspector and the right-click menu also offer changing an agent's prompt, removing or restoring an agent, adding an agent, and changing the shared goal. A change to recorded history or a past cursor creates a branch. A change at an experiment branch's head appends to that branch. The branch menu in the top bar switches branches; the `⋯` menu holds analysis, import, and Git checkpoints. Splitter sizes and view filters persist in this browser.

The local demo's `Timeline demo · coordination prompt` branch was created through the UI at event 5,996 to verify this flow. The recorded walkthrough also creates `Demo · verify before acting` at event 3,008. Each has one prompt intervention. The Recorded branch retains all 6,048 events. These local branches are not committed to the source repository.

## Framework boundaries

```text
examples/ai_village                 source meaning, selection, application wiring
web/                               HTTP and browser presentation
adapters/                          SQLite, Git, content-addressed files
application/                       branch/replay/intervention/plugin use cases + ports
core/                              entities, events, deterministic state rules
```

Dependencies point inward. The core and application layers import no HTTP server, database, model SDK, Git process, or AI Village code. The core has no third-party dependencies. Browser modules separate the timeline, graph, inspector, forms, and application coordination.

- **State:** agents, channels, messages, memory, tool calls, and an environment.
- **History:** runs, branches, ordered immutable events, and snapshots.
- **Extensions:** application-owned `Source`, `Runtime`, `Plugin`, `HistoryStore`, and `VersionStore` protocols.

See [architecture](docs/architecture.md), [developer integration](docs/integration.md), and [AI Village investigation](docs/ai-village.md).

## What works today

SQLite persistence and relational projections; replay at any event cursor; nested forks with shared history; add/remove/edit agents; branch prompt, memory, channel, and goal interventions; versioned plugin analysis; a runtime extension point; Git checkpoints; raw response inspection; and the interactive explorer.

The AI Village example reconstructs observations. A changed prompt does not generate new model responses or restore a remote desktop. Implement `Runtime.continue_from` to continue a branch using your execution environment. Probes, SAEs, NLAs, and steering belong in application plugins/runtime adapters that have access to the required model internals; this export does not contain those internals.

This is a working foundation, not a claim of deployment readiness at arbitrary scale. Before exposing it to a team, supply application authentication and deployment configuration. A larger workload needs paged history/state reads, snapshot retention, and asynchronous runtime jobs. These changes fit the existing outer adapters and application services.

## Comments and sharing

Researchers can comment on any event of a branch, reply in threads, and resolve threads, much as in Google Docs. Comments are not history events. They are stored in their own table and never change replay, state, or forks. A comment belongs to the branch where it was written. A branch also shows its ancestors' comments anchored at or before its fork point. A reply answers a thread's top-level comment and shares its event. Only a top-level comment can be resolved. Deleting it deletes its replies.

In the explorer, select an event and press **C** (or use **Comment** in the inspector or the right-click menu). The first comment asks for your name, which stays in this browser; change it with **⋯ → Your name…**. Lanes marks commented events above the lanes (**View → Show resolved** includes resolved threads), the transcript shows a count badge, and the inspector lists the threads of the selected event, or all open threads when nothing is selected. Visualizations receive the comments as `context.comments`. **⋯ → Export run** downloads the bundle and **⋯ → Import run…** opens it as a new run.

**Export** writes a run as one JSON bundle (`swarm-lens.run`, version 1). The bundle holds the run's name and metadata, its branch tree (each branch's fork position and own events), and its comment threads. Branches use local keys and comments use event positions, so the bundle holds no database ids. **Import** creates a new run in one database transaction. It replays each branch through the reducer once, and a child starts from its parent's state at the fork. If any event or comment anchor is invalid, nothing is written. A bundle can have at most 256 branches, 64 levels of nested forks and 200,000 events in total. An unknown format or version is rejected. Artifacts referenced by digest (for example, raw model responses) are not included. Their digests are kept as they are, so copy `artifacts/` with the bundle if the receiver needs them.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/api/branches/{id}/comments` | `{comments: [Comment]}`, ordered by event position, then creation time |
| POST | `/api/branches/{id}/comments` | `{event_id, author, text, parent_id?}` → 201 `Comment` |
| PATCH | `/api/comments/{id}` | `{text?, resolved?}` → `Comment` |
| DELETE | `/api/comments/{id}` | → 204 (a top-level comment deletes its replies) |
| GET | `/api/runs/{run_id}/export` | Bundle as a JSON download (`<run name>.swarm-lens.json`) |
| POST | `/api/runs/import` | Bundle (64 MiB maximum) → 201 `{branch}` (the new root branch) |

A `Comment` is `{id, branch_id, event_id, position, author, text, created_at, parent_id, resolved, updated_at}`. `author` has 1 to 80 characters. `text` cannot be blank and has at most 10,000 characters. Invalid requests return 400. The server rejects all mutations (POST, PATCH, DELETE) from other origins.

## Observability methods

For LLM tracing, use [MAST](docs/observability/mast/README.md): install `.[web,mast]`, configure the server's `OPENAI_API_KEY`, and launch the usual explorer. **Import trace** (core, `POST /api/traces`) saves a transcript locally without calling a model; **Analyze with MAST** runs the upstream 14-category judge on a saved snapshot and retains the result. MAST does not run live. Its sub-API is `/api/plugins/mast`; CASPIAN has no dedicated API.

Optional methods live under `swarm_lens.observability`, independently of the core. The first is a paper-based [CASPIAN implementation](docs/observability/caspian/README.md) with streaming conditional influence estimation, spectral detection, and role/path attribution. Install `.[caspian]`. Applications supply observed source/target vectors through a versioned history adapter; no AI Village schema is embedded in the method.

The [paper coverage map](docs/observability/caspian/coverage.md) records equation-level tests, missing author artifacts, reconstruction choices, and mathematical limitations of the published rules. This is not a reproduction of the paper's reported benchmark accuracy or latency. Runnable synthetic, attribution, and nested-branch examples are under `examples/observability/caspian/`; `python -m examples.ai_village.caspian` audits the Village mapping's missing evidence without fabricating scores. See the [method extension contract](docs/observability/README.md).

## Frontend development

The explorer uses compiled Tailwind CSS and accessible [Zag.js](https://zagjs.com/) select/combobox components. The conversation picker supports search, keyboard navigation, and capture metadata to distinguish repeated run names. Styles live in `frontend/css/`: `base.css` holds the design tokens (light and dark), shared controls, and the page shell; each feature module has its own file. Agent messages everywhere (transcript, inspector, Compare, MAST evidence) render through one formatter, `frontend/message-format.js` (Markdown-it and KaTeX, recorded HTML stays text). It is bundled to `src/swarm_lens/web/message-format.js` and loaded on first use, so startup does not wait for it.

The area above the transcript hosts interchangeable visualizations (Lanes, Influence, and others) that share the cursor, selection and playback controls. A web plugin adds one with `host.registerVisualization({ id, title, mount(root, actions, toolbar) })`, where `mount` returns `{ update(context), destroy() }` and may add `stepTarget(delta)`. `context` is `{ events, branch, state, cursor, selectedId, agents }`; `actions` offers `seek`, `select`, `selectAgent`, `contextMenu`, `togglePlayback` and `loadDetail`. Only the visible view is mounted: switching away calls `destroy()`, so hidden views do no work. A web plugin lists its saved analysis jobs in **Reports** with `host.addReports({ title, prefix, render(root, job), analyze })`; jobs come from `GET {prefix}/analyses?branch_id=…` and `GET {prefix}/analyses/{id}`, and `host.openReport(job)` opens one. Installers are keyed by the manifest's `ui.renderer` in `pluginRenderers` (`src/swarm_lens/web/app.js`).

```sh
npm ci
npm run build
npm test
```

Rebuild after editing frontend styles or `frontend/components.js`. Commit the generated `src/swarm_lens/web/tailwind.css` and `components.js` alongside their sources. These assets ship with the Python package, so running the application requires neither Node.js nor a CDN.

## Validation

```sh
python -m pytest -q
```

For the supplied Conda runtime, disable its incompatible pytest capture and OpenMP initialization after fork:

```sh
KMP_INIT_AT_FORK=FALSE PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:capture -q
```

Tests cover nested historical forks, restart/replay, isolation, stale/concurrent writes, atomic batches, relational references, tool lifecycle, runtime extension behavior, plugin mutation, Git parentage, and API cursor boundaries. Browser checks cover event menus, expansion/resizing, filters, and an actual intervention branch with a verified diff.

## Separate data-adapter tooling

A useful next tool is an agent-assisted application generator: give it a source schema and sample rows; it writes a `Source` implementation, provenance mappings, and representative fixtures for a developer to review. That tool should live separately from this framework. The developer owns how their source maps into agents, channels, tasks, ordering, and state. The framework then runs the application using those explicit decisions.

AI Village data is attributed to AI Digest / AI Village. Local provider icons come from LobeHub Icons under its [MIT license](src/swarm_lens/web/logos/LICENSE); names and logos identify providers. See [third-party notices](THIRD_PARTY.md).

### ACIArena LLM Debate pilot

Run the pinned upstream benchmark with paired benign/name-disclosure cases and real OpenAI embeddings. See [setup, protocol, and limitations](examples/aciarena/README.md). CASPIAN remains inside this repository.
