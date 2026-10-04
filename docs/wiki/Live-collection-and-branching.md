# Live collection and branching

## Playback, live collection, and branch execution

These three get confused, so it's worth being precise:

- **Playback** steps through history that's already saved. No model is called.
- **Live collection** records an agent application while it runs and streams each new event to the explorer.
- **Branch execution** takes a saved conversation up to some point and continues it with real model and tool calls, using a compatible runtime.

## Run the CrewAI example

The integration is tested against CrewAI 1.15.23 on Python 3.12. From the repository root:

```sh
python3.12 -m venv .venv-crewai
source .venv-crewai/bin/activate
python -m pip install -r examples/crewai/requirements.lock
python -m pip install --no-deps -e .
export OTEL_SDK_DISABLED=true
export CREWAI_TELEMETRY_DISABLED=true
export CREWAI_TRACING_ENABLED=false
export CREWAI_STORAGE_DIR="$PWD/data/crewai-storage"
swarm-lens --data data --port 8765 \
  --runtime examples.crewai.demo:create_runtime \
  --trace-tools examples.crewai.demo:trace_tools
```

In a second terminal, with the same environment activated:

```sh
python -m examples.crewai.demo --offline --url http://127.0.0.1:8765
```

`--offline` runs the real CrewAI loop but swaps in fixed, deterministic responses, so it's free. Drop the flag (and set `OPENAI_API_KEY`) to use a real model, which you'll pay for.

## Instrument your own crew

Wrap your kickoff call with `swarm_lens.integrations.crewai.observe`, passing the crew, its inputs, and a registered runtime. Your application stays in charge of the crew factory, the tool implementations, and its own revision. The [integration example](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/examples/crewai/README.md) walks through the full setup, including observer configuration and which kinds of state aren't supported yet.

## Continue a saved conversation

Select an event, pick a fork action, check the runtime preview, and choose **Create and run**. Whatever you changed (a goal, a prompt) applies only to the new branch. The parent branch, including everything after the fork point, is left alone.

How the run resumes depends on where the trace came from. For a trace captured natively from CrewAI, the adapter can pick up at a task boundary or restart the current task with the recorded context. An imported trace gets rebuilt as a CrewAI crew: ACIArena LLMDebate keeps its debater-then-aggregator order, and anything else takes turns round-robin. Either way, this is a new run that starts from the recorded messages. The original framework's internal state isn't restored.

Tools need some care. They have to be registered explicitly, and a saved tool result isn't the same as calling the tool again. Forking also won't undo anything a tool did to files or external services, and if a task restarts it may call those tools a second time. The preview tells you, before anything runs, which tools are available and which agent will act next.
