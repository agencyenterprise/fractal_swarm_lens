# Live collection and branching

## Three different operations

- **Playback** advances through saved history. It makes no model call.
- **Live collection** records a running agent application and streams new events to the explorer.
- **Branch execution** creates new model/tool activity from a saved prefix using a compatible runtime.

## Run the pinned CrewAI example

The integration is compatibility-tested with CrewAI 1.15.23 and Python 3.12. From the repository root:

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

In another terminal with the same environment:

```sh
python -m examples.crewai.demo --offline --url http://127.0.0.1:8765
```

This runs the real CrewAI loop with deterministic fixture responses. Omit `--offline` for model execution after configuring `OPENAI_API_KEY`; that incurs usage.

## Instrument an existing crew

Wrap kickoff with `swarm_lens.integrations.crewai.observe`, passing the crew, inputs, and a registered runtime. The application owns the factory, tool implementations, and revision. Follow the complete [integration example](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/examples/crewai/README.md), including observer configuration and unsupported-state restrictions.

## Continue a saved conversation

Select an event, choose a fork action, review the runtime preview, and select **Create and run**. A goal or prompt change belongs to the new branch. The parent and its later events remain intact.

For a native capture, the adapter can resume at task boundaries or restart the active task with recorded context. Imported traces are reconstructed in CrewAI. ACIArena LLMDebate uses its debater/aggregator ordering; other imported traces use an explicit round-robin policy. This is not restoration of the original framework's hidden process state.

Tool implementations must be registered explicitly. Saved tool results are not new executions. Forking does not roll back external files or services, and a restarted task may call tools again. The preview explains availability and the next actor before execution.
