# Swarm Lens

A self-hosted framework for observing, replaying, branching, and intervening in agent systems. The included AI Village application demonstrates how a developer maps one source system into the framework's primitives. Dataset interpretation belongs to that application.

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

The timeline is the primary view. Each agent has a lane and provider logo. Message bubbles, tool actions, memory writes, and interventions have distinct markers. Connections show broadcasts into real channels. Scrub the playhead or bottom slider, zoom in and out, and toggle event types. Drag the bottom divider to resize the timeline; Expand shows more lanes. Height persists in this browser. Messages, Tools, Memory, and All events sit directly beneath the timeline with their search filter. The swarm graph and inspector share a separate side panel; the graph and panel width also resize.

Click or right-click an event for its context menu. Create a branch there, change its agent's prompt, remove or restore an agent, add an agent, or change the shared goal. A change to recorded history or a past cursor creates a branch. A change at an experiment branch's head appends to that branch. Compare branches and save Git checkpoints from the toolbar. Keyboard users can focus an event and use Enter or Shift+F10; the timeline divider accepts Up/Down and the playhead accepts Left/Right.

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

## Observability methods

Optional methods live under `swarm_lens.observability`, independently of the core. The first is a paper-based [CASPIAN implementation](docs/observability/caspian/README.md) with streaming conditional influence estimation, spectral detection, and role/path attribution. Install `.[caspian]`. Applications supply observed source/target vectors through a versioned history adapter; no AI Village schema is embedded in the method.

The [paper coverage map](docs/observability/caspian/coverage.md) records equation-level tests, missing author artifacts, reconstruction choices, and mathematical limitations of the published rules. This is not a reproduction of the paper's reported benchmark accuracy or latency. Runnable synthetic, attribution, and nested-branch examples are under `examples/observability/caspian/`; `python -m examples.ai_village.caspian` audits the Village mapping's missing evidence without fabricating scores. See the [method extension contract](docs/observability/README.md).

## Validation

```sh
python -m pytest -q
```

For the supplied Conda runtime, which has a `readline`/pytest capture incompatibility:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:capture -q
```

Tests cover nested historical forks, restart/replay, isolation, stale/concurrent writes, atomic batches, relational references, tool lifecycle, runtime extension behavior, plugin mutation, Git parentage, and API cursor boundaries. Browser checks cover event menus, expansion/resizing, filters, and an actual intervention branch with a verified diff.

## Separate data-adapter tooling

A useful next tool is an agent-assisted application generator: give it a source schema and sample rows; it writes a `Source` implementation, provenance mappings, and representative fixtures for a developer to review. That tool should live separately from this framework. The developer owns how their source maps into agents, channels, tasks, ordering, and state. The framework then runs the application using those explicit decisions.

AI Village data is attributed to AI Digest / AI Village. Local provider icons come from LobeHub Icons under its [MIT license](src/swarm_lens/web/logos/LICENSE); names and logos identify providers. See [third-party notices](THIRD_PARTY.md).
