# CrewAI examples and integration

[Repository quickstart](../../README.md#quickstart) · [Example index](../README.md) · [MAST analysis](../../docs/observability/mast/README.md)

Run these commands from the repository root. This guide uses one `data` workspace and port **8767** throughout. Install once, reuse the environment, and try the offline crew before making real model calls.

Supported and compatibility-tested: **CrewAI 1.15.23**, Python 3.12. The optional adapter uses CrewAI's supported [LLM/tool hooks](https://docs.crewai.com/en/learn/llm-hooks) and [memory events](https://docs.crewai.com/en/concepts/event-listener). The dependency lock is `requirements.lock` in this directory.

## Start the application

```sh
python3.12 -m venv .venv-crewai
source .venv-crewai/bin/activate
python -m pip install -r examples/crewai/requirements.lock
python -m pip install --no-deps -e .
# For the included example, disable CrewAI's separate telemetry.
export OTEL_SDK_DISABLED=true
export CREWAI_TELEMETRY_DISABLED=true
export CREWAI_TRACING_ENABLED=false
export CREWAI_STORAGE_DIR="$PWD/data/crewai-storage"
swarm-lens --data data --port 8767 \
  --runtime examples.crewai.demo:create_runtime \
  --trace-tools examples.crewai.demo:trace_tools
```

`--runtime MODULE:FACTORY` imports trusted local application code at startup. Repeat it to register more runtimes. The HTTP API never imports a module supplied by a browser or dataset. Starting without a runtime still supports live capture, saved playback, and MAST analysis.

Load a conversation, select an event on the timeline, and choose **Fork at cursor** or **Run from here…**. Name the branch, optionally change its goal or other supported state, and choose **Create branch** or **Create branch and run live**. Both actions create a child at the selected cursor in the same conversation, including when the source is already a branch. Native CrewAI captures restore a task boundary or restart the active task from saved context; imported conversations use the continuation policies below. If a native crew has finished all tasks, select an earlier event. The UI shows an animated running indicator and elapsed time, then a persistent completion/failure notification with a link to the branch. Pending jobs remain watched if you switch conversations or reload the page.

To capture your first example, in a second terminal activate the same environment and run:

```sh
python -m examples.crewai.demo --url http://127.0.0.1:8767           # real GPT-5.5 calls
python -m examples.crewai.demo --offline --url http://127.0.0.1:8767 # no model calls
```

The example has three agents and three tasks: calculate 19 + 23 using a Python tool, verify, and report. The second command is explicitly an offline fixture, not an LLM experiment. Captures appear automatically in the conversation picker. **Follow live** advances with incoming events; turning it off retains the selected historical cursor while collection continues. **Replay** only advances through existing history.

## Observe your existing crew

```python
from swarm_lens.integrations.crewai import CrewAIRuntime, observe

# make_crew(inputs) constructs a fresh Crew; use the same factory on the server.
runtime = CrewAIRuntime("my-crew", make_crew, revision="my-app-git-commit")
inputs = {"topic": "Your task"}
crew = make_crew(inputs)
with observe(crew, inputs=inputs, runtime=runtime,
             url="http://127.0.0.1:8767", name="My task") as capture:
    result = crew.kickoff(inputs=inputs)
print(capture.branch_id)
```

Use the same `inputs` for observation and kickoff. For a concise alternative, `@traced_crew(runtime=runtime)` decorates a `factory(inputs) -> Crew`; calling it constructs, observes, and invokes the native kickoff. Capturing does not require registering a runtime; registration is needed to execute a saved branch.

For programmatic creation of an independent capture through `/api/live/apps/{id}/start`, configure the registered runtime with `launch_inputs={...}`, `title="My application"`, `description="What this run does and which models it calls"`, and optionally `required_env=("OPENAI_API_KEY",)`. These inputs come from trusted server configuration; the browser cannot supply code or choose a factory. This bootstrap API creates a conversation; the UI's live execution action uses `/fork-execute` and always forks the selected conversation. Both accept stable request IDs, so retries do not repeat model or tool calls. Failed runs keep their recorded events; a server restart marks unfinished jobs interrupted.

The adapter records model input snapshots, model responses, tool arguments/results, completed task outputs, agent configuration, a shared task channel, native memory notifications with agent/task identity, and framework/runtime versions. Model input snapshots contain the actual context, so they can establish what a later task received. Channel membership by itself does not establish delivery or causality. Observed agent/backstory and goal fields are separate from the full generated system prompt in each input snapshot.

Scope: a synchronous `Crew.kickoff` observed within the context manager. Do not share the same crew instance across simultaneous captures. Concurrent independent instances are tested. Flows, detached work after the context exits, token streaming, and arbitrary direct LLM calls without agent identity are outside this adapter's capture contract.

## Execute a branch

1. Load a conversation and select a position on the timeline. Recorded CrewAI task boundaries appear in the **Resume points** lane.
2. Choose **Fork at cursor**, or click a timeline point and choose **Run from here…**. To change behavior, choose **Change shared goal…**, edit an agent, or edit a completed task output from its memory inspector.
3. Name the branch and choose **Create branch** or **Create branch and run live** in the same dialog. Both actions fork exactly at the selected event and include the proposed change. The live action validates the proposed state before creating the branch, then executes up to 100 remaining tasks. Inside a task, the UI explains that the current task starts a fresh execution using context saved through the cursor; subsequent tasks then run normally. Unsupported configurations and edits show a reason. The fork never moves to another event.
4. New events stream into the child; parent history stays unchanged. A spinner shows execution in progress and a completion banner remains visible until dismissed. Pausing Follow live does not stop the job or its completion notification.

A fresh instance of the registered factory is reconstructed. At a task boundary, completed task outputs are restored and supplied through CrewAI's task context; only remaining selected tasks run through native `Crew.kickoff`. This does not rely on CrewAI's machine-wide "latest kickoff" replay database. New outputs become new checkpoints, so a branch can run one task at a time and itself be forked.

Supported restoration is deliberately explicit: sequential, synchronous text tasks, fixed agent membership/connections, factory-provided tools, and no external CrewAI memory/knowledge/planning, delegation, conditional tasks, human input, guardrails, callbacks, or task output files. Unsupported state and interventions produce an actionable error before execution. Inside a task, its recorded responses, relevant memories/model-input snapshot, and the agent's saved tool observations are supplied as context to a new native task execution. The child keeps the exact selected prefix and records a `crew.task.restarted` event. Later parent messages and tool results are excluded. This reconstructs context without restoring the in-process executor or treating pending tool calls as completed. Task-output edits are supported; arbitrary memory edits are rejected.

Tool implementations come from the trusted factory. Forking does not roll back files, databases, or other external tool side effects, and a resumed task can call those tools again. Application factories should use an isolated test environment where needed. The app does not claim that an imported ACIArena or arbitrary transcript is a CrewAI checkpoint.

The saved manifest and explicit application revision are checked against the factory before execution. Increase the revision when tools or behavior change. Keep factories side-effect free: constructing one should not invoke models/tools. A job is queued and the branch locked against edits while it runs. Failed/interrupted jobs retain saved events and are never automatically rerun after restart.

## Continue an imported conversation

The CLI also registers `TraceCrewAIRuntime`, so imported examples can use **Create branch and run live** without an original CrewAI factory. Install the `crewai` extra and configure `OPENAI_API_KEY`. Each active saved agent becomes a native CrewAI agent with its name, system prompt, model and registered tools. Missing model names use `SWARM_LENS_CONTINUATION_MODEL` (default `gpt-5.5`). The current task, changed goal, connected conversation messages, private agent memories and shared memories become task context. Prompt-capture memories are excluded from feedback to avoid recursively duplicating the same context. Agent outputs and native tool events stream into the child under the original agent IDs. Private channel messages and other agents’ private memories are not supplied.

The preview displays the policy, next actor, number of turns and models:

- **ACIArena LLM Debate:** continue missing bootstrap responses, then debaters in numerical order using the latest available peer responses; aggregate only after the final debate round. A mid-round fork preserves its exact next debater. Original examples retain their configured 20-round endpoint. Forking an already aggregated conversation adds 20 debate rounds followed by aggregation. Recorded every-input injections are retained only for the recorded target, and only if installed before the cursor. Changed connections control subsequent delivery.
- **Other imported traces:** one round over active agents in saved order, starting after the last recorded speaker. Each turn broadcasts its output to that agent’s current channels. A later turn sees earlier connected responses from the same round. Fork again to extend the conversation.

This is a **new execution reconstructed in CrewAI**, not an exact replay of the source framework or a reproduction of benchmark scores. External memory stores, hidden prompts, scheduling rules and tool side effects cannot be recovered from text alone. Models may call tools again. The original trace and its later events remain untouched and are excluded from fork context. Each new execution records its scheduling policy and a versioned continuation checkpoint; a fork inside an unfinished turn restarts that turn with the selected saved context.

Recorded tool names need explicit, trusted executable mappings. Unknown tools block live preflight before branch creation; saved results are never treated as new tool executions. Supply a factory with `--trace-tools MODULE:FACTORY` returning `{recorded_name: callable_returning_fresh_crewai_tool}`. For the local arithmetic tool:

```sh
python -m swarm_lens.cli --data data --port 8767 \
  --runtime examples.crewai.demo:create_runtime --trace-tools examples.crewai.demo:trace_tools
```

Application code can register `TraceCrewAIRuntime(framework, tools=tool_factories)` in `LiveService` directly. Tool code is never loaded from traces, URLs, model output or browser-submitted module names.

## AI Village: Perform novel research!

The bundled example contains the public replay of [AI Village's “Perform novel research!” goal](https://theaidigest.org/village/goal/perform-novel-research): five sessions, **May 11–15, 2026**, with **15 agents, 2,222 chat messages and 3,680 source events**. Attribution: AI Digest / AI Village. The compressed recording is about 4.5 MB; only these sessions and their roster were retrieved, without downloading the full dataset.

Import it into the same data directory used by your server:

```sh
python -m examples.crewai.ai_village_research import --data data
```

Reload the conversation picker and select **AI Village · Perform novel research! · May 11–15**. Importing again reuses the recording. The manifest and original source events are in `examples/crewai/samples/ai-village-novel-research-20260511/`; checksums and source event IDs preserve provenance. Chat messages retain their native timestamps, speakers and rooms. Public activity such as session plans becomes an observation, not an invented memory or tool execution. Additional setup and room-membership events mean the SwarmLens event count is larger than the source count.

To prepare a child for CrewAI continuation, use the branch `id` printed by the import command:

```sh
python -m examples.crewai.ai_village_research prepare \
  --data data --branch YOUR_RECORDED_BRANCH_ID --model gpt-5.5
```

Optionally add `--cursor <event-number>` to fork earlier in the recording. This creates **CrewAI continuation · gpt-5.5** and explicitly changes each known agent's model on that child. The original recording and its model metadata remain intact. Neither command calls an LLM. In the UI, select the child and choose **Fork at cursor → Create branch and run live** to generate a new continuation. The CLI's standard trace runtime runs one round over active agents using their saved room connections; it requires CrewAI and `OPENAI_API_KEY`.

This is a continuation from recorded public chat. Original computer-use sessions, screenshots, private memories, system prompts and executable tools were not fetched or reconstructed. Observed room presence does not prove historical message delivery. The example does not reproduce the original village execution or extend its research with the original agents' computers.

To refresh only the five public sessions into a new directory:

```sh
python -m examples.crewai.ai_village_research fetch \
  --output /tmp/ai-village-research-refresh
```

Offline validation (including a real CrewAI loop with a deterministic model fixture):

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OTEL_SDK_DISABLED=true \
CREWAI_TELEMETRY_DISABLED=true CREWAI_TRACING_ENABLED=false \
  .venv-crewai/bin/python -m pytest -p no:capture -q tests/integrations/test_ai_village_research.py
```


## Delivery and recovery

Every collector batch is written to a local JSONL outbox before HTTP delivery. Network errors warn visibly and leave the task running; pending events can be replayed. An acknowledged write is durable. Event IDs, source digests, and expected sequence numbers make a lost acknowledgement retry idempotent; a reused ID with different content is rejected.

```python
from swarm_lens.live.client import LiveClient
recovered = LiveClient.recover("data/capture-outbox/<capture-id>.jsonl",
                              "http://127.0.0.1:8767")
assert not recovered.offline
```

Outboxes include full trace content; keep them with the application's private data. They are excluded from Git by the default `data/` location. An outbox with no final status is recovered as incomplete. Local disk failure is reported as incomplete capture; the agent loop continues after hook failures.

The WebSocket stream reads committed history with a resumable cursor and bounded pages. The browser reconnects from its last processed event. Historical selection is independent of the stream. Capture completion and job status are persisted separately from socket connectivity. Use one API process/worker for this local implementation; self-hosting on an untrusted network requires authentication at a reverse proxy. Default binding is loopback.

Endpoints:

- `GET /api/live/apps`: registered launchable applications and configuration readiness, without credentials.
- `POST /api/live/apps/{runtime_id}/start`: launch a new conversation using a UUID `request_id` for idempotent retries.
- `POST /api/live/runs`: idempotent capture creation.
- `POST /api/live/branches/{id}/events`: atomic event batches.
- `POST /api/live/branches/{id}/finish`: completed, failed, or incomplete status.
- `GET /api/live/branches/{id}/events?after=N`: committed catch-up page.
- `WS /api/live/branches/{id}/stream?after=N`: events and capture/job status.
- `GET /api/branches/{id}/execution?cursor=N`: compatibility, checkpoints, and jobs.
- `POST /api/branches/{id}/execution/preview`: validate a cursor and optional `{kind, data}` intervention without saving or executing anything.
- `POST /api/branches/{id}/execute`: enqueue a continuation with expected head and task count.
- `POST /api/branches/{id}/fork-execute`: validate the selected `cursor` and optional `intervention`, create a child with `name` in the same conversation, apply the change, and execute `steps`; a UUID `request_id` deduplicates retries. This is the UI execution endpoint.
- `GET /api/executions/{id}`: persisted execution result/status.

MAST remains an explicit saved-trace analysis. It is never called per live event. CASPIAN remains experimental and has no additional API.

## Verification

```sh
KMP_INIT_AT_FORK=FALSE PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/integrations/test_crewai.py tests/test_live.py -q -p no:capture
node --test tests/web/live.test.mjs
```

Tests run actual CrewAI orchestration with a deterministic LLM fixture, including real tool execution, launch-to-fork continuation, independent simultaneous crews, task-boundary restoration, prompt and task-memory changes affecting new outputs, parent isolation, rejected unsupported edits/configuration drift, atomic ingestion, lost-ack recovery, launch retry deduplication, WebSocket catch-up, and durable failure/interrupted-job status.

The browser-state tests cover running indicators, fast completion, persistent completion/failure notices, duplicate status delivery, and pending notifications surviving a reload or navigation to another conversation. The API test also verifies that fork execution from an existing child retains the conversation ID and parent history, with idempotent retries and no branch created for an invalid checkpoint.
