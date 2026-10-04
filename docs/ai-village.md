# AI Village: Perform novel research!

The maintained example is the [public replay of “Perform novel research!”](https://theaidigest.org/village/goal/perform-novel-research), attributed to **AI Digest / AI Village**. It is bundled under [`examples/crewai/samples/ai-village-novel-research-20260511/`](../examples/crewai/samples/ai-village-novel-research-20260511/) and imported by [`examples.crewai.ai_village_research`](../examples/crewai/ai_village_research.py).

## Selected data

The bundle contains the five public replay sessions for **May 11–15, 2026**. The upstream goal itself spans May 11–18; the bundle should not be described as a complete reconstruction of every underlying activity during that interval.

| Record | Count |
| --- | ---: |
| Observed agents | 15 |
| Public replay events | 3,680 |
| Agent chat messages | 2,146 |
| Human chat messages | 76 |
| Other public activity events | 1,458 |
| Imported Swarm Lens events | 3,715 |

Swarm Lens adds explicit environment, agent, channel, and membership facts needed to replay the native records. Preparing a full-recording continuation adds 15 model-change interventions on a **child**, producing a head of 3,730; it does not add generated conversation messages.

The compressed event file is about **4.5 MB**. Retrieval used date-filtered public API pages and roster metadata, approximately 14.9 MB in total. It did not download the entire AI Village dataset or require a Hugging Face token. Counts, selected dates, source URLs, page hashes, event-file checksum, retrieval time, and roster are recorded in the [manifest](../examples/crewai/samples/ai-village-novel-research-20260511/manifest.json).

## Import and continue

From the repository root, with the [CrewAI environment](../README.md#quickstart) activated:

```sh
python -m examples.crewai.ai_village_research import --data data
```

The command prints a recorded branch `id`. The importer validates the source and resulting facts, retains full native event payloads as artifacts, and reuses the existing matching recording on reimport.

To substitute GPT-5.5 explicitly for subsequent CrewAI execution:

```sh
python -m examples.crewai.ai_village_research prepare \
  --data data --branch YOUR_RECORDED_BRANCH_ID --model gpt-5.5
```

Add `--cursor EVENT_NUMBER` to branch earlier. Neither import nor preparation invokes a model. Select the prepared child in the UI, then **Fork at cursor → Create branch and run live** when ready to generate output. The generic imported-trace policy runs one round over active agents using their saved room connections.

## Mapping and limits

- Native `eventIndex` determines chronological order. Source IDs, timestamps and full payloads remain available.
- Agents and rooms enter state on their first observed event. Room membership changes follow observed room activity rather than inventing all-to-all connections. Presence is not proof of message delivery or causality.
- Chat messages preserve their content, speaker, original message identity, and room. Agent names and model strings come from the roster at retrieval and are labeled with that provenance; they are not a guarantee about every historical model invocation.
- Session consolidation, pauses, history searches and other public activity become observations. A session plan or quoted command is not converted into a verified tool execution or private memory.
- Original computer-use turns, screenshots, system prompts, private memories and executable tools were not fetched. The source recording is an observation of the public replay, not a CrewAI checkpoint or a restored remote computer.
- A continuation substitutes models on a child and reconstructs chat context. It is a new experiment, not an exact replay of the original village agents.

## Refresh only these sessions

Use a **new** output directory to preserve the existing bundle:

```sh
python -m examples.crewai.ai_village_research fetch \
  --output /tmp/ai-village-research-refresh
python -m examples.crewai.ai_village_research import \
  --source /tmp/ai-village-research-refresh --data data
```

The fetcher checks requested dates, pagination, duplicate IDs, ordering and event limits before writing the manifest. It does not retrieve large underlying table dumps or screenshots.

The older gated leader-election example has been retired. Already imported workspaces remain readable; their stored histories and artifacts do not depend on keeping that old downloader in the examples directory.
