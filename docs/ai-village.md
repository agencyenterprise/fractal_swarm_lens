# AI Village investigation and example

Source: [AI Digest's AI Village dataset](https://huggingface.co/datasets/aidigestorg/ai-village), pinned revision `838b4150303ca8228e8edb432d8b8ccae353d258`.

The example selects the opening day of the village goal “Elect a village leader. They choose this week’s goal!” (`b11a446c-869f-4cbb-b8dd-7e45c2a01735`). The interval is January 5, 2026, 17:34:13.998 UTC through January 6, 00:00 UTC, exclusive. Observed activity runs approximately 17:59–22:02 UTC. This is one shared-context excerpt, not the complete week's goal. Its discussions progress into the leader's chosen interactive-fiction project.

## Selected records

| Source | Selected rows | Interpretation |
|---|---:|---|
| Active agent identities | 10 | Framework agents |
| Chat rooms used | 1 | Shared `general` channel |
| Chat messages | 605 | Message facts linked to native activity events |
| Native activity events | 1,104 | Messages and other observations |
| Computer-use sessions | 132 | Parent context attached to turns |
| Computer-use turns | 4,034 | 3,989 executed actions and 45 model-only observations |
| Agent-memory rows | 898 | Revisions of ten operational memory slots |

The resulting history has **6,048 events**: 3,989 tools, 898 memory revisions, 605 messages, 544 observations, ten agent initializations, one channel initialization, and one environment initialization.

The ten agents are Claude 3.7 Sonnet, Claude Haiku 4.5, Claude Opus 4.5, Claude Sonnet 4.5, DeepSeek-V3.2, GPT-5, GPT-5.1, GPT-5.2, Gemini 2.5 Pro, and Gemini 3 Pro.

## Mapping decisions

- The export does not have one task foreign key across all these tables. The application selects a goal's opening-day interval and resolves referenced computer sessions, including one parent session that began earlier. Temporal membership is explicitly recorded in the selection manifest.
- Native `event_index` determines activity order. Table files are not ordered by time. The selected native events have no timestamp inversions. Memory and computer turns are inserted between those anchors by UTC timestamps, with deterministic ties. Cross-table ordering is inferred, not a native causal ordering.
- A chat message is imported once, at its linked native activity event. Both source identities are retained. Shared-channel broadcasts are visualized as broadcasts; mentions do not imply verified direct delivery or causality.
- One stable memory ID per agent represents the exported operational memory. All 898 revisions remain in history. No pre-window memory was fetched, so memory is unknown before its first observed revision.
- Agent names/models come from the exported roster, which is not guaranteed to describe every historical call. Per-call provider response payloads are preserved separately as artifacts. Missing system prompts remain `null`.
- Computer turns record completed observed actions. The export does not provide a reliable start timestamp for each action, so the timeline does not invent execution durations.
- Model response payloads are available in the inspector. Screenshots were not downloaded. Model weights, activations, exact execution environments, and original system prompts are not reconstructed.

## Reproduce the import

The local demo workspace contains the selected source JSON files; they are excluded from Git. On a fresh clone, fetch them using the command below first. To produce a separate clean workspace from an existing selection:

```sh
PYTHONPATH=src python3 -m examples.ai_village.app --source data/source --data data-rebuilt
PYTHONPATH=src python3 -m swarm_lens.cli --data data-rebuilt --port 8766
```

To fetch again, use a new output directory. The fetch script prompts for a Hugging Face token without echoing it, or reads `HF_TOKEN` from the environment. No token is saved. Dataset access must already be granted by Hugging Face.

```sh
PYTHONPATH=src python3 -m examples.ai_village.fetch --output data-refetched/source
```

For the exact same revision, place the revision hash above in `data-refetched/source/revision.txt` before running the command. The extractor streams compressed source tables, retaining only selected rows and metadata rather than saving the full dataset. Existing selected files are treated as a resumable extraction; use a new directory for a different interval.

`data/source/selection.json` records the selection and its limitations; `data/import-report.json` records the imported run, event count, and initial Git checkpoint.

## CASPIAN readiness

`python -m examples.ai_village.caspian --source data/source` audits this application's exported schema without changing it or emitting payloads. The current mapping lacks verified recipient exposure/response pairing and cross-agent memory/tool lineage. It therefore produces a readiness report, not inferred influence or attack scores. Model internals are not required by CASPIAN, but observed downstream behavior and explicit feature/turn mappings are. See [the CASPIAN contract](observability/caspian/README.md).
