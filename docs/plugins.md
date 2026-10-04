# Writing a plugin

A plugin is a small Python object that reads a branch and returns results. The application validates and stores those results. The explorer then shows them with no plugin-specific frontend code: metrics become tracks under the agent lanes, annotations become markers on the timeline, and interventions become fork actions. A plugin never gets a database handle, and it never changes an existing branch.

The [reference plugins](../examples/plugins/reference.py) are the shortest complete example. Copy them.

## The contract

```python
from pydantic import BaseModel, ConfigDict, Field
from swarm_lens.core.models import Fact
from swarm_lens.plugins import Annotation, Metric, Report


class MyPlugin:
    id, version = "my-plugin", "1.0.0"         # required; the id is a lowercase letter, then letters, digits, hyphens
    title = "My plugin"                        # optional; shown in the UI (the default is the id)
    description = "One line for the UI."       # optional

    class Params(BaseModel):                   # optional; without it the plugin takes no params
        model_config = ConfigDict(extra="forbid")
        threshold: float = Field(0.5, ge=0, le=1, description="Shown as help text in the form")

    def analyze(self, view, start, end, params):        # Analyzer
        ...                                              # yield Metric, Annotation, and at most one Report

    def on_events(self, view, events, params):          # StreamingAnalyzer
        ...                                              # yield Metric and Annotation

    def intervene(self, view, at, params):              # Intervention
        return [Fact("agent.removed", {"id": params.agent_id}, view.state_at(at).occurred_at)]


def create(services):
    return MyPlugin()
```

A plugin implements any subset of the three protocols. The registry detects them with `isinstance`, so do not subclass anything. `params` is the validated `Params` instance, or `None` when the plugin has no `Params`.

| Protocol | Method | What the app does with the result |
| --- | --- | --- |
| `Analyzer` | `analyze(view, start, end, params) -> Iterable[Metric \| Annotation \| Report]` | Runs as a background job over events `start..end` and stores one analysis record. |
| `StreamingAnalyzer` | `on_events(view, events, params) -> Iterable[Metric \| Annotation]` | For incremental methods. It is not wired into live ingestion yet. A plugin that is only streaming runs on demand: the job feeds events `start..end` in order, in batches of 100, with one view per run. Keep no state on `self`, because one plugin instance serves concurrent jobs on different branches. Recompute what you need from `view.events()`, or write an `Analyzer`. Per-run streaming state is the open question for live wiring. |
| `Intervention` | `intervene(view, at, params) -> Sequence[Fact]` | Checks the facts through the reducer on a private copy of the state at `at`. Then it forks at `at` and appends them. The parent never changes. |

### BranchView

A plugin reads everything through a `BranchView`. The view stops at the analyzed `end` (or the intervention point `at`), so a plugin cannot read events from later in the branch.

| Member | Returns |
| --- | --- |
| `branch_id`, `head` | The branch, and the last position the view can read |
| `state_at(seq)` | The state after event `seq` (0 is empty), as a private copy |
| `events(start=1, end=None, *, kind=None, agent_id=None)` | Events `start..end`, inclusive. `kind` is an exact kind (`"message.created"`) or a family prefix (`"message."`). `agent_id` matches an agent event's own ID, else the sender, the tool caller or the memory owner. |
| `metrics(*, plugin=None, name=None, agent_id=None)` | Stored metrics that this branch can see (see below), only from analyses that read no event after the view's `head` |
| `annotations(*, plugin=None, start=None, end=None)` | Stored annotations under the same rule, overlapping `start..end` |

`state_at` and `events` return fresh copies, so editing them changes nothing else, not even the next `events()` call. Stored findings are read once per view, so a long job sees one consistent set. The analyses whose findings a view handed out are recorded in the new record's `analyses_read`.

Plugins are trusted code running in the server process. The view is a contract, not a sandbox: it holds only read functions, and the app applies whatever a plugin returns.

### Findings

```python
Metric(seq, name, value, agent_id=None)
Annotation(seq_from, seq_to, label, agent_id=None, score=None, data={}, cited_event_ids=())
Report(data)
```

- A **Metric** is a number at one event position. Give it an `agent_id` for a per-agent series. The UI draws one track per plugin, name and agent.
- An **Annotation** labels the inclusive span `seq_from..seq_to`. `score` is optional, and the UI shows it on hover. `data` is free JSON. `cited_event_ids` are the IDs of the events that support the annotation.
- A **Report** is your own JSON for a custom view, at most one per analysis. Use it for results that do not fit metrics or annotations, such as summaries, matrices, model metadata or raw responses.

The app sets `plugin` on every finding. Positions must lie inside `start..end`. Values and scores must be finite numbers. Names, labels and agent IDs must be nonempty strings, and `data` must be JSON. Cited event IDs must belong to events 1..`end` of the branch. A violation fails the job with a clear message, and nothing is saved.

Each analysis is stored as an ordinary analysis record (`cursor` is `end`), with no schema change:

```json
{"id": "...", "plugin_id": "my-plugin", "plugin_version": "1.0.0", "branch_id": "...",
 "start": 1, "end": 120, "cursor": 120, "config": {"threshold": 0.5}, "input_digest": "<sha256 of events 1..end>",
 "analyses_read": [], "created_at": "...",
 "output": {"format": "swarm-lens.findings/v1", "metrics": [...], "annotations": [...], "report": null}}
```

**Which findings a branch shows:** for each plugin, the findings of its latest finished analysis that the branch can see. A branch sees its own analyses. It also sees its ancestors' analyses that end at or before the fork, because only those read history that the branch shares. To replace what the UI shows, run the plugin again.

### Params

`Params` is a normal pydantic model. The HTTP API exposes `Params.model_json_schema()`, and the UI renders a form from it. The form supports strings, multi-line strings (`Field(json_schema_extra={"format": "textarea"})`), numbers, integers with bounds, booleans, enums (`Literal[...]` or `Enum`), and optional values of those types. Invalid params are rejected with HTTP 400 before a job exists. The record keeps the params as requested (`model_dump(mode="json", by_alias=True)`, taken before the plugin runs), so field aliases round-trip.

## Registering a plugin

A factory takes `PluginServices` and returns a plugin, a `WebExtension`, or a list or tuple of them. `services.data` is the data directory and `services.artifacts` is the content-addressed artifact store. A method plugin must not use `services.framework` or `services.plugins`: those are for web routes. Pass model clients and other services to your plugin's constructor.

You can register a factory in two ways. Duplicate plugin IDs fail at startup.

```toml
# pyproject.toml of your package: `pip install` is enough
[project.entry-points."swarm_lens.plugins"]
my-plugin = "my_package.my_plugin:create"
```

```sh
swarm-lens --data data --plugin my_package.my_plugin:create   # repeatable
```

Bundled plugins (Activity, MAST, [Influence ribbon](plugins/influence-ribbon.md) and [Change points](plugins/change-points.md)) are listed in `swarm_lens/cli.py` (`BUNDLED_PLUGINS`).

## What the UI does for free

- **Analyze menu** (top bar, next to **Fork here**): lists every analyzer. Choosing one opens a form generated from `Params`, with first and last events (the default is 1 to the current cursor). Running it starts a job. The menu's **Recent analyses** shows each job's status, and a toast reports when the job finishes or fails.
- **Metric tracks:** one sparkline per series under the agent lanes, aligned with the event positions. Turn them on or off with **View → Metric tracks**.
- **Annotation markers:** spans on the ruler, or in the agent's lane when `agent_id` is set. Hover shows the label, the plugin and the score, and a click selects the first event of the span. Turn them on or off with **View → Annotations**.
- **Inspector → Findings:** for the selected event, the annotations that cover or cite it, and the metric values at that position.
- **Fork with…:** every intervention appears in the event context menu and in the inspector's fork actions. It opens the params form and a branch name, then opens the new branch.

A plugin that needs a custom view can still ship a browser module: return a `WebExtension` with `assets/index.js` next to the plugin (see [write a web plugin](integration.md#write-a-web-plugin)). It can read its own `Report` from `job.analysis.output.report`. MAST does this for its report view.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/plugins` | `{"plugins": [{id, version, title, description, capabilities, params_schema}]}` |
| POST | `/api/branches/{id}/analyses` | `{plugin_id, start, end, params}` → 202 and a queued job |
| GET | `/api/branches/{id}/analyses?plugin_id=` | The branch's jobs from every plugin, newest first |
| GET | `/api/analyses/{plugin_id}/{job_id}` | One job; its `analysis` field holds the record when the job finishes |
| GET | `/api/branches/{id}/series?plugin=&name=&agent=&max_points=` | Metric series as `[seq, value]` points. Above `max_points` (default 2000), each bucket keeps its lowest and highest point. |
| GET | `/api/branches/{id}/annotations?from=&to=&plugin=` | Annotations that overlap the range |
| POST | `/api/branches/{id}/interventions/{plugin_id}` | `{at, name, params}` → 201 and the new branch |

Jobs are durable SQLite records (`data/jobs.sqlite`, one table per plugin). Each plugin allows at most four pending jobs. If the server restarts, its queued and running jobs are marked interrupted and are never repeated. A job's `error` never contains a provider's exception message. An exception's own `detail` attribute (set by the model clients that this application writes) is kept in `error_detail` for diagnosis, so never put secrets in it. A `DomainError` message is shown as written, so raise one for errors that the user can fix.

## Testing a plugin

Run the plugin through the real app against a small fixture branch. `tests/conftest.py` provides the `framework` and `branch` fixtures (7 events: a goal, agent `a`, channel `c`, two messages and two memory writes). `tests/test_plugins.py` exercises the reference plugins in the same way.

```python
from fastapi.testclient import TestClient
from swarm_lens import PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.web.api import create_app
from my_package.my_plugin import MyPlugin


def test_my_plugin_marks_the_second_message(framework, branch, tmp_path):
    plugins = PluginService(framework, (MyPlugin(),),
                            jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    client = TestClient(create_app(framework, plugins=plugins))
    job = client.post(f"/api/branches/{branch.id}/analyses",
                      json={"plugin_id": "my-plugin", "end": 7, "params": {"threshold": 0.2}}).json()
    assert client.get(f"/api/analyses/my-plugin/{job['id']}").json()["status"] == "completed"
    annotations = client.get(f"/api/branches/{branch.id}/annotations").json()["annotations"]
    assert [a["seq_from"] for a in annotations] == [7]
```

`TestClient` runs the background job before it returns the response, so the job has finished when you read it. To test without HTTP, call `PluginService(framework, (MyPlugin(),)).analyze("my-plugin", branch.id, 1, 7, {...})`. It returns the stored record. With a job store, an inline run is also recorded as a completed job, so other plugins' views and the UI see its findings.

## Porting a plugin from `run(context, config)`

- Rename `run(context, config)` to `analyze(view, start, end, params)`. `context.history()` becomes `view.events(start, end)`, and `context.state` becomes `view.state_at(end)`.
- Return `Metric` and `Annotation` items for anything that has a position. Put the old free-form dict in one `Report(...)`. It is then at `record["output"]["report"]`.
- Turn the `config` dict into a `Params` model.
- `context.fork()` and `context.intervene()` become an `Intervention` that returns facts.
- `Framework(plugins=...)` and `Framework.analyze` are gone. Use `PluginService(framework, plugins).analyze(plugin_id, branch_id, start, end, params)`.
- A web plugin with its own job flow takes its job store from `services.plugins.jobs(plugin_id, label)` and saves through `services.plugins.save(...)`, so its findings appear in the generic UI. MAST does this (`observability/mast/service.py`).
