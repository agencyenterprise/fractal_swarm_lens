# Import saved medical messageboard runs

This adapter imports an **existing** multi-agent simulator batch, one conversation per world. It is retained for the medical-data examples. It does not run the simulator or call a model.

Use the environment from the [CrewAI quickstart](../../README.md#quickstart). The input directory must contain the simulator's `manifest.json` and the event files listed in each manifest entry. Those external batch files are not bundled with this example.

```sh
source .venv-crewai/bin/activate
python -m examples.messageboard.source \
  --input /path/to/saved-messageboard-batch \
  --data data
```

Start or reload the explorer using the **same** `--data` directory:

```sh
swarm-lens --data data --port 8767 \
  --runtime examples.crewai.demo:create_runtime \
  --trace-tools examples.crewai.demo:trace_tools
```

Each conversation is named `Messageboard · <run id> · <status>`, with a featured-case suffix where available. The importer verifies event counts and records the source-log digest. Already imported batch/run/digest combinations are skipped. Changing the dataset bytes represents a different import.

## What is preserved

- Recorded order, original event IDs/types, timestamps, and round numbers.
- Agent model, provider, prompt, tools, and simulator configuration.
- Private simulator deliveries and LLM responses in agent-specific inboxes; private responses are not turned into shared posts.
- Tool invocations and results, with their recorded arguments and status.
- Board observations, task/outcome records, and other simulator events as observations with the original record attached.

The UI can replay, compare, comment on, and analyze these runs. New CrewAI execution requires executable mappings for **all** recorded tool names and compatible agent/provider configuration. The arithmetic demo's calculator registry does not implement the medical simulator's shell or HTTP tools. Inspect the live-execution preview before attempting continuation.

For adapter implementation, see [source.py](source.py); for another schema, see the [source integration contract](../../docs/integration.md).
