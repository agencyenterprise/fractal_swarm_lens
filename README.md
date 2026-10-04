<p align="center">
  <img src="src/swarm_lens/web/logos/swarm-lens.gif" alt="Swarm Lens: robots in a factory observed through a lens" width="112" />
</p>

<h1 align="center">Swarm Lens</h1>

<p align="center"><strong>Observe agent systems. Explore their history. Run a different path.</strong></p>
<p align="center">A local workspace for live CrewAI traces, recorded conversations, branching experiments, and MAST analysis.</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#examples">Examples</a> ·
  <a href="#connect-your-crewai-application">Integrate</a> ·
  <a href="#mast-analysis">MAST</a> ·
  <a href="docs/integration.md">Plugin guide</a>
</p>

![Capture → Inspect → Branch → Analyze. Python 3.12 recommended · CrewAI 1.15.23 · FastAPI · SQLite](assets/docs/workflow.svg)

Swarm Lens gives messages, tools, memories, interventions, and connections one shared event history. Capture a running crew or import a saved trace, inspect what happened at any event, then fork an experiment without rewriting the original conversation. New CrewAI execution streams back into the same workspace.

| Explore | Experiment | Understand |
| :--- | :--- | :--- |
| Timeline, transcript, agent state, tools, and memory at a selected cursor | Nested branches, goal/prompt changes, and supported CrewAI continuation | MAST's 14 failure modes, saved reports, evidence spans, and chunked analysis |
| Follow live or pause to inspect history | Compare an original run with an alternative | Add plugin APIs, views, actions, and visualizations |

**Current integration:** CrewAI **1.15.23**, compatibility-tested with Python **3.12**. The core library supports Python 3.11+ and has no mandatory third-party dependencies. The UI and API run locally in one process; browser assets ship with the package.

## Contents

- [Quickstart](#quickstart)
- [Examples](#examples)
- [Connect your CrewAI application](#connect-your-crewai-application)
- [Replay, fork, and run live](#replay-fork-and-run-live)
- [MAST analysis](#mast-analysis)
- [Workspace and collaboration](#workspace-and-collaboration)
- [Configuration](#configuration)
- [Plugins and APIs](#plugins-and-apis)
- [Architecture](#architecture)
- [Development and verification](#development-and-verification)
- [Troubleshooting](#troubleshooting)
- [Documentation and project status](#documentation-and-project-status)

## Quickstart

### 1. Install once

Use Python 3.12 and Git. Run example commands from the repository root; `examples/` is source-checkout code rather than an installed Python package.

```sh
git clone https://github.com/agencyenterprise/fractal_swarm_lens.git swarm-lens
cd swarm-lens

python3.12 -m venv .venv-crewai
source .venv-crewai/bin/activate
python -m pip install -r examples/crewai/requirements.lock
python -m pip install --no-deps -e .
```

The lock includes the CrewAI, web, MAST, and development dependencies. You do **not** need Node.js, a frontend build, a GPU, or the upstream benchmark submodules to try the bundled recordings.

For a fresh checkout, create the local environment file:

```sh
cp .env.example .env
```

Keep an existing `.env` when upgrading. Set `OPENAI_API_KEY` only when you want live model execution or MAST analysis. Importing, replaying, branching without execution, and the offline CrewAI example do not call an LLM.

### 2. Load a recording

```sh
python -m examples.crewai.ai_village_research import --data data
```

This imports **“Perform novel research!”**: 15 agents and 2,222 chat messages from five AI Village sessions, May 11–15, 2026. The selected public recording is bundled as a roughly 4.5 MB compressed file; no full dataset download or Hugging Face token is needed. Re-running the import reuses the same recording.

### 3. Start the workspace

```sh
swarm-lens --data data --port 8767 \
  --runtime examples.crewai.demo:create_runtime \
  --trace-tools examples.crewai.demo:trace_tools
```

Open **http://127.0.0.1:8767**. Select the AI Village conversation to explore it. Stop the server with `Ctrl+C`; start it with the same `--data` directory to reopen your workspace.

The runtime registration enables the arithmetic crew's saved branches to execute. The tool registry supplies its calculator when continuing an imported trace. The CLI also registers a general CrewAI continuation runtime for compatible saved conversations.

### 4. Try live collection without model costs

Keep the server running. In a second terminal at the repository root:

```sh
source .venv-crewai/bin/activate
python -m examples.crewai.demo --offline --url http://127.0.0.1:8767
```

Select **CrewAI arithmetic · offline compatibility test** in the conversation picker. CrewAI runs its real three-task loop and Python calculator tool with a deterministic model fixture. This checks collection and replay; it is not an LLM benchmark.

For a real GPT-5.5 run, configure the server's and runner's `OPENAI_API_KEY` in `.env`, then omit `--offline`:

```sh
python -m examples.crewai.demo --url http://127.0.0.1:8767
```

New captures appear in the picker. **Follow live** advances with incoming events; turning it off lets you inspect history while collection continues. Runs can finish quickly—completion status is saved even if you open the conversation afterward.

## Examples

Start with saved data, then use the offline crew, then enable real models when you need new behavior. Each example has a different purpose:

| Example | Command / guide | Makes model calls? | Use it for |
| :--- | :--- | :---: | :--- |
| CrewAI arithmetic, offline | `python -m examples.crewai.demo --offline --url http://127.0.0.1:8767` | No | Check real CrewAI orchestration, tool capture, replay, and branching |
| CrewAI arithmetic, GPT-5.5 | Same command without `--offline` | Yes | Capture a live three-agent application |
| AI Village research recording | [Import and prepare a continuation](examples/crewai/README.md#ai-village-perform-novel-research) | No during import/preparation | Inspect a longer conversation and create a CrewAI continuation branch |
| ACIArena control / injection pairs | [Recorded benchmark examples](examples/aciarena/README.md) | No during import | Contrast matched conditions; includes math, medicine, and code |
| Medical messageboard logs | [Import an existing batch](examples/messageboard/README.md) | No | Preserve simulator events, private inboxes, tools, and board observations |

### Continue the AI Village example with CrewAI

The import prints the recorded branch's `id`. Use it to prepare an explicit model-substitution branch:

```sh
python -m examples.crewai.ai_village_research prepare \
  --data data --branch YOUR_RECORDED_BRANCH_ID --model gpt-5.5
```

Add `--cursor EVENT_NUMBER` to prepare a branch earlier in the recording. Select **CrewAI continuation · gpt-5.5** in the UI, choose **Fork at cursor**, and choose **Create branch and run live**. Preparation itself makes no model calls. The child uses GPT-5.5 for each known agent; the original recording retains its roster/model metadata.

This executes a new conversation from saved public chat. It does not restore AI Village's original computers, private memories, system prompts, or tools. See the [source and mapping notes](docs/ai-village.md).

### Import the medical ACIArena pair

Use the same virtual environment and data directory:

```sh
python -m examples.aciarena.samples import \
  --input examples/aciarena/samples/llm-debate-medicine-misalign-20261004 \
  --data data
```

Choose the two `medicine-000` conversations and use **Compare**. Their saved manifest records their original model, task, conditions, and outcomes. Importing never regenerates answers. The [ACIArena guide](examples/aciarena/README.md) separates this inexpensive workflow from optional upstream generation and research experiments.

**Example cleanup:** the previous gated AI Village leader-election downloader and standalone CASPIAN demo CLIs have been retired. The selective research recording replaces the former onboarding example. Numeric helpers needed by CASPIAN regression tests live under `tests/support/`; the experimental method remains in the library. Existing local workspaces and research results are unaffected. See the [example index](examples/README.md).

## Connect your CrewAI application

Wrap a synchronous kickoff with `observe`. Your application keeps its agents, tasks, and orchestration:

```python
from swarm_lens.integrations.crewai import observe

# crew and inputs are defined by your application.
with observe(
    crew,
    inputs=inputs,
    url="http://127.0.0.1:8767",
    name="My research crew",
) as capture:
    result = crew.kickoff(inputs=inputs)

print(capture.branch_id)
```

This captures model inputs/responses, tools, task outputs, agent configuration, supported memory events, and runtime metadata. Batches first enter a durable local outbox; acknowledged events are persisted by the server. `@traced_crew` is available for factory-based applications.

To **execute a captured branch**, also register a versioned factory that constructs a fresh compatible crew:

```python
from swarm_lens.integrations.crewai import CrewAIRuntime

def create_runtime():
    return CrewAIRuntime("research-crew", make_crew, revision="research-crew-v1")
```

Pass that runtime to `observe(..., runtime=create_runtime())` and start the server with `--runtime your_package.application:create_runtime`. Increase the revision when the factory's behavior or tools change. Factories must not call models or tools while being constructed.

Supported continuation focuses on synchronous, sequential text tasks with explicit tool implementations. CrewAI Flows, arbitrary external memory, and restoring a remote machine are outside the current contract. The [CrewAI guide](examples/crewai/README.md) covers supported edits, task restart semantics, tool registration, decorators, and outbox recovery.

## Replay, fork, and run live

| Action | What happens |
| :--- | :--- |
| Play / scrub the timeline | Moves through already recorded events; no model execution |
| Follow live | Follows new events from a running capture; collection continues when disabled |
| Create branch | Saves the selected history prefix and proposed intervention |
| Create branch and run live | Saves that child, validates execution support, then runs CrewAI and records new events |

1. Select a conversation and a timeline event.
2. Choose **Fork at cursor** / **Run from here…**, or an intervention such as **Change shared goal**.
3. Name the branch and choose whether to save it or also run it live.
4. Inspect the result in Timeline, then compare it with the parent. New events belong to the child; later parent events never enter its context.

There are two execution paths:

- **Native CrewAI capture:** restore a supported task boundary, or restart the active task using context saved through the exact selected event. The runtime never silently moves your cursor to another checkpoint.
- **Imported trace:** reconstruct native CrewAI tasks from the saved agent state and connections. Generic traces use one round over active agents. ACIArena debate traces use their recorded debater/aggregator scheduling policy. Saved tool names require trusted executable mappings on the server.

The UI previews the policy, models, next actor, and remaining work. Unsupported state produces a reason before execution. External tool side effects are not rolled back when you fork; use an isolated application environment for experiments that modify external resources.

## MAST analysis

From the `⋯` menu, choose **Analyze with MAST…**, select trace completeness, and inspect the size preview. Analysis is an explicit action on a saved branch/cursor; it does not run on every incoming event.

MAST returns a summary, task-completion judgment, and assessments for **14 failure modes**. Expand a trait to inspect supporting events, surrounding context, and counterevidence. Each report has a stable link, a JSON download, model/prompt provenance, and a link back to the analyzed timeline.

**Oversized traces are handled automatically: version 0.4.0 splits → analyzes → reconciles.**

- Split in chronological event order, with up to two overlapping events when they fit. Oversized individual events are preserved as exact fragments.
- Assess and localize evidence in each chunk within the configured character and model token budgets.
- Reconcile findings across chunks, including contradictions and later corrections. Larger report collections use further bounded reconciliation passes.
- Save every completed stage and one combined report. The UI shows chunk/reconciliation progress. Failed or interrupted stages never become an implicit all-clear.

The default judge is GPT-5.5. The upstream notebook used `o1`; chunking and the selected model are explicit extensions of that protocol. Reconciliation uses chunk summaries and localized evidence, so it is not guaranteed to match a whole-trace judgment. These remain LLM assessments, not verified cascade labels. See the [full MAST guide](docs/observability/mast/README.md) for taxonomy ambiguities, request budgeting, persistence, and limitations.

## Workspace and collaboration

**Timeline** combines agent/channel lanes, a readable transcript, and an inspector for messages, tools, memories, and interventions. Its main visualization can be swapped while keeping the shared cursor. **Compare** contrasts two branches or recorded conditions. **Reports** holds plugin analyses. Markdown and mathematical notation render throughout message and evidence views, with access to original text.

**Comments** attach to events without changing replay. Threads support replies and resolution; descendants show ancestor comments only through their fork points. Press `C` on a selected event or use the inspector. The display name is local browser identity, not account authentication.

**Export run / Import run** transfers a run's branch tree and comment threads as a validated JSON bundle. Referenced artifact files and plugin job stores are not embedded. Copy the workspace's artifacts and plugin data separately if the recipient needs raw payloads and saved plugin reports. Report URLs work for people with access to the same server; a localhost URL stays local.

### Persistent data

| Path under `--data` | Purpose |
| :--- | :--- |
| `history.sqlite` | Runs, branches, ordered events, snapshots, comments, and analysis records |
| `artifacts/` | Content-addressed raw payloads and frozen analysis inputs/results |
| `live.sqlite` | Capture sessions and execution jobs |
| `mast.sqlite` | MAST job status, stages, and saved reports |
| `history.git/` | Optional application checkpoints, separate from source control |

Collectors default to an outbox at `data/capture-outbox` relative to their own process; configure `spool_dir` for a separate application. Your data, environment files, and provider credentials are excluded from Git.

Use a different `--data` directory to test a clean workspace rather than deleting existing history. Before migrating an older workspace, stop **all** servers, collectors, and scripts that use it and back up the entire directory. Storage v2 migrates v1 on first open and must not be opened with the older code afterward. See [storage and migration details](docs/architecture.md#sqlite-and-git).

## Configuration

| Setting | Default / purpose |
| :--- | :--- |
| `--data` | `data`; choose the same workspace for the server and import commands |
| `--host` / `--port` | Loopback / `8765` in the CLI; this guide consistently uses `8767` |
| `--env-file` | `.env`; server configuration, never sent to the browser |
| `--runtime MODULE:FACTORY` | Register trusted native CrewAI factories; repeatable |
| `--trace-tools MODULE:FACTORY` | Map saved tool names to trusted executable tools |
| `--plugin MODULE:FACTORY` | Register additional web/API plugins; repeatable |
| `OPENAI_API_KEY` | Needed for the real GPT-5.5 example and OpenAI-backed MAST |
| `MAST_MODEL` | `gpt-5.5` |
| `MAST_CONTEXT_WINDOW` | Override the context window for an unrecognized model |
| `MAST_MAX_TRACE_CHARACTERS` | `4000000` per request; larger traces are chunked |
| `SWARM_LENS_CONTINUATION_MODEL` | `gpt-5.5` fallback for imported agents without a recorded model |

Imported agents retain their saved model unless you explicitly change it on a branch. Changing the fallback alone does not replace existing agent models. The AI Village `prepare` command makes that substitution explicit.

The arithmetic example disables CrewAI's separate telemetry and tracing. For another application, use its own telemetry settings. Keep one Swarm Lens API process per workspace; built-in authentication and Docker/Compose packaging are not currently included.

## Plugins and APIs

The FastAPI application serves the UI, history API, live ingestion, WebSockets, and plugin routes together. Browse **http://127.0.0.1:8767/docs** for the actual registered API.

| Area | Selected routes |
| :--- | :--- |
| Workspace | `GET /api/workspace` |
| Saved data | `POST /api/traces`, `GET /api/branches/{id}/timeline`, `GET /api/branches/{id}/state` |
| Branch execution | `GET /api/branches/{id}/execution`, `POST /api/branches/{id}/fork-execute` |
| Live collection | `POST /api/live/runs`, `POST /api/live/branches/{id}/events` |
| Live transport | `WS /api/live/branches/{id}/stream?after=N` |
| MAST | `POST /api/plugins/mast/preview`, `POST /api/plugins/mast/analyses` |
| Sharing | `GET /api/runs/{id}/export`, `POST /api/runs/import` |

A web plugin owns its namespaced router and can ship a browser module. The UI discovers registered manifests on startup and installs their actions, report views, and visualizations. It does not hardcode a list of plugin-specific screens. Plugin loading failures are isolated; installing or removing a plugin requires restarting/reloading the application. MAST is bundled by the standard CLI; custom application composition can choose its own extensions.

See [write a web plugin](docs/integration.md#write-a-web-plugin) and [the CrewAI API reference](examples/crewai/README.md#delivery-and-recovery).

## Architecture

```text
CrewAI application ── observation hooks ──┐
                                        ├── ordered event history ── API / WebSocket ── explorer
Saved recording ── explicit source ──────┘             │
                                            branch / execute / analyze
```

```text
src/swarm_lens/
  core/                       Entities, events, deterministic state rules
  application/                Replay, branching, interventions, bundles, extension ports
  adapters/                   SQLite, Git checkpoints, artifact storage, embeddings
  integrations/crewai/        Capture, native runtime, imported-trace continuation
  live/                       Durable capture and execution services
  observability/              MAST and experimental CASPIAN methods
  web/                        FastAPI, browser explorer, and web plugins
examples/                     CrewAI workflows and retained dataset importers
tests/support/                Numeric research regression fixtures
vendor/                       Pinned upstream repositories and separate research code
```

The core/application layers do not import FastAPI, databases, model SDKs, or dataset-specific code. Dataset adapters own source interpretation; runtimes own real execution. A channel connection is not by itself proof that one agent consumed another's output.

## Development and verification

Use the installed CrewAI environment. Tests use deterministic or mocked models; they do not validate MAST or CASPIAN's empirical accuracy.

```sh
# Pinned upstream sources are needed by source-parity/benchmark tests, not the quickstart.
git submodule update --init vendor/mast vendor/aciarena

OTEL_SDK_DISABLED=true CREWAI_TELEMETRY_DISABLED=true CREWAI_TRACING_ENABLED=false \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:capture -q
```

Optional benchmark dependency tests may skip when their separate environment is not installed. For a focused CrewAI check:

```sh
OTEL_SDK_DISABLED=true CREWAI_TELEMETRY_DISABLED=true CREWAI_TRACING_ENABLED=false \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:capture -q \
  tests/integrations/test_crewai.py tests/integrations/test_ai_village_research.py
```

Node.js is needed only for frontend development. Use Node 22.13+ on the 22.x line, or Node 24+, as required by the locked browser test dependencies:

```sh
npm ci
npm run build
npm test
```

The UI uses Tailwind CSS, accessible Zag.js controls, and locally bundled Markdown/MathML rendering. Rebuild after changing `frontend/`; commit generated assets alongside their sources. Browser code for a web plugin lives in that plugin's `static/` directory. [Frontend and plugin contracts](docs/integration.md#write-a-web-plugin).

## Troubleshooting

| Symptom | Check |
| :--- | :--- |
| A colleague sees different conversations | Compare `--data` directories. SQLite workspaces are local; switching source branches does not exchange conversations. Import a recording or run bundle. |
| A new import is missing | Reload the picker and confirm the importer and server used the same `--data` path. |
| A capture cannot reach the server | Match the runner's `--url` to the actual port. The demo defaults to 8766; commands here explicitly use 8767. Check its printed outbox path. |
| Play works but live execution is unavailable | Play replays saved events. Check the execution preview, runtime revision, provider configuration, and tool mappings. |
| No live continuation after the native crew finishes | Select an earlier task/event to branch, or use a compatible imported-trace continuation policy. |
| MAST says instructions cannot fit | Increase the per-request limit or correct `MAST_CONTEXT_WINDOW`. Chunking still needs room for the fixed taxonomy, examples, and response reservation. |
| MAST job failed or was interrupted | Inspect saved stages in Reports. A new analysis is explicit; restart does not automatically repeat paid requests. |
| Old conversation data remains after an update | This is expected persistence. Use a new workspace or import the newer examples; no SQLite reset is required. |

## Documentation and project status

| Guide | Covers |
| :--- | :--- |
| [Examples](examples/README.md) | Maintained examples, cost-aware workflow, and cleanup scope |
| [CrewAI](examples/crewai/README.md) | Collection, factories, continuation, tool mappings, delivery, and recovery |
| [AI Village](docs/ai-village.md) | Selected source, event mapping, provenance, and missing evidence |
| [MAST](docs/observability/mast/README.md) | Saved-trace analysis, chunking, evidence, and plugin API |
| [Integration](docs/integration.md) | Source, runtime, analysis, and web extension contracts |
| [Architecture](docs/architecture.md) | Event invariants, storage, migration, and deployment priorities |
| [ACIArena](examples/aciarena/README.md) | Saved benchmark pairs and optional generation/research |
| [CASPIAN](docs/observability/caspian/README.md) | Experimental method, input contract, and unvalidated claims |

**Implemented:** saved and live trace workflows, CrewAI capture/continuation, nested branches, comparison, comments, run bundles, plugin UI/API registration, MAST reports and chunked reconciliation, and storage v2 migration.

**Still limited:** single-process local deployment; no built-in authentication or Docker configuration; no guarantee of exact restoration for external tools, remote computers, or arbitrary agent frameworks. CASPIAN remains available as an experimental library module without a dedicated web API. Its cascade-detection accuracy has not been established, and cascade effects are not necessarily harmful behavior.

AI Village material is attributed to AI Digest / AI Village; ACIArena and MAST retain their upstream provenance. Provider icons identify model families and do not imply endorsement. See [third-party notices](THIRD_PARTY.md) and [branding assets](assets/branding/swarm-lens-robot-factory-v1/README.md).
