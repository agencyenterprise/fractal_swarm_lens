# Examples and datasets

## The medical debate from the demo

From the repository root on `main`, with your virtual environment active:

```sh
python -m pip install -e '.[web,aciarena,mast]'
git submodule update --init vendor/aciarena
python -m examples.aciarena.samples import \
  --input examples/aciarena/samples/llm-debate-medicine-misalign-20261004 \
  --data data
swarm-lens --data data --port 8765
```

The `aciarena` extra pulls in some heavy ML dependencies; if you'd rather keep them out of your main environment, the [ACIArena guide](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/examples/aciarena/README.md) uses a separate `.venv-aciarena`.

This imports files that are already in the repo. It doesn't regenerate them and doesn't call a model.

In the run picker you'll find two runs: **ACIArena · LLMDebate · medicine-000 · Without attack · 20 rounds** and the same run **With malicious agent**. Each has 64 model responses: three opening answers, 20 rounds of three-agent debate, and a final aggregation. The question is about dental impression materials. It's a benchmark task, not medical advice.

The malicious agent is upstream's `MisalignAgent`. In both runs, the native grader marked the final answer incorrect and the attack unsuccessful. Those two labels don't tell you whether anything spread between agents; that's a separate question you'd answer by reading the trace.

## Other bundled examples

| Folder under `examples/aciarena/samples/` | What's in it |
| --- | --- |
| `llm-debate-pair-20261003` | Math debate: control vs. name-leak injection |
| `mad-medicine-misalign-20261004` | Medical MAD control/attack pair; the moderator settles it after the opening answers, so there are no debate rounds |
| `llm-debate-code-malicious-report-20261004` | Code scenario, GPT-4o mini |
| `llm-debate-code-malicious-report-gpt4o-20261004` | Code scenario, GPT-4o |
| `llm-debate-code-malicious-report-gemini31pro-20261004` | Code scenario, Gemini via OpenRouter |

Import any of them with the same command, swapping in the folder name. For MAD, "20 rounds" is the maximum, not a promise; the moderator can end the debate early, and in the bundled pair it ends before the first round.

## Generating your own data

The generator covers math, medicine, and code scenarios, runs control and attack conditions in pairs, and can run them concurrently. It needs API credentials and makes paid model calls. The [ACIArena guide](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/examples/aciarena/README.md) has the commands, budgets, and manifest format, and explains how upstream attacks differ from our local `medicine-wrong-option` extension.

A few datasets aren't in the repo: the 30-task wrong-option batch, the message-board dataset, and AI Village (which needs dataset access to import). They won't appear after a fresh clone.

## What to keep for each experiment

Hold on to the manifest, the source revision, trace hashes, model settings, the exact inputs and outputs, the delivery links between agents, and the native grader output. Upstream's option-letter extraction occasionally misreads an answer, so look at the raw response before trusting a grader label.
