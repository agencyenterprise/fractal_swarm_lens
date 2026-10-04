# Examples and datasets

## Medical debate from the demo

From the repository root on `main`, with your virtual environment active:

```sh
python -m pip install -e '.[web,aciarena,mast]'
git submodule update --init vendor/aciarena
python -m examples.aciarena.samples import \
  --input examples/aciarena/samples/llm-debate-medicine-misalign-20261004 \
  --data data
swarm-lens --data data --port 8765
```

This imports saved files; it does not regenerate them or call a model. Choose **ACIArena · LLMDebate · medicine-000 · Without attack · 20 rounds** or **With malicious agent**. Each has 64 model responses: three initial responses, 20 three-agent debate rounds, and aggregation. The question concerns dental impression materials; it is a benchmark scenario, not clinical guidance.

The malicious-agent example uses upstream `MisalignAgent`. Both bundled medical runs were scored incorrect by the native task grader and the attack was scored unsuccessful. These labels do not establish whether a cascade occurred.

## Other bundled examples

| Folder under `examples/aciarena/samples/` | Content |
| --- | --- |
| `llm-debate-pair-20261003` | Math debate, control versus name-leak injection |
| `mad-medicine-misalign-20261004` | Medical MAD control/attack pair; moderator stops at bootstrap |
| `llm-debate-code-malicious-report-20261004` | Code scenario with GPT-4o mini |
| `llm-debate-code-malicious-report-gpt4o-20261004` | Code scenario with GPT-4o |
| `llm-debate-code-malicious-report-gemini31pro-20261004` | Code scenario with Gemini via OpenRouter |

Use the same import command with the desired folder. A 20-round configuration is an upper schedule parameter for MAD, not a guarantee of 20 completed rounds: the bundled MAD pair stops early.

## Generating new data

The generator supports math, medicine, and code scenarios, paired conditions, and concurrent execution. Generation requires credentials and makes paid model calls. See the [versioned ACIArena guide](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/examples/aciarena/README.md) for commands, budgets, manifests, and the distinction between upstream attacks and the local `medicine-wrong-option` extension.

The 30-task wrong-option batch and the separate message-board dataset are not bundled. The AI Village importer requires dataset access. Do not expect those conversations to appear after cloning.

## Evidence to retain

Keep the manifest, source revision, trace hashes, model settings, exact inputs/outputs, delivered-source links, and native grader outputs. Some upstream option-letter extraction can misread an answer. Inspect raw responses before treating a grader label as established truth.
