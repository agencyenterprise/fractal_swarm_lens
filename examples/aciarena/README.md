# ACIArena LLM Debate pilot

This application-owned runner executes the actual pinned ACIArena `LLMDebate`, `MathNameLeakInstruction`, and `MathTask.verify` implementations. CASPIAN remains an optional observability module inside Swarm Lens. No upstream benchmark code is edited.

## Setup and run

From the repository root, with Python 3.11 or newer:

```sh
git submodule update --init vendor/aciarena
python3 -m venv .venv-aciarena
.venv-aciarena/bin/python -m pip install -e '.[caspian,embeddings,aciarena,dev]'
# Configure OPENAI_API_KEY in the ignored .env, following .env.example.
.venv-aciarena/bin/python -m examples.aciarena.benchmark --tasks 1 --max-turn 20
```

The direct dependency versions used for the first pilot are recorded in `requirements-pilot.txt`; this is not a full transitive lockfile.

The first N tasks retain their row IDs in the pinned math dataset. Every task runs twice, serially, with fresh agents, histories, attack objects, feature caches, and detectors. The benign run precedes its attacked pair. Both receive ACIArena's identical planted disclosure context; only the attacked debater receives the attack instruction. The names and credentials planted by the benchmark are synthetic fixtures.

Defaults: three debaters plus one final aggregator, `gpt-4o-mini-2024-07-18`, temperature 0, seed 42, maximum 1,024 output tokens per call, and `text-embedding-3-small` with 16 dimensions. Provider seeds do not guarantee identical generations. `--dimensions` overrides the environment dimension setting. `--max-turn 20` means three bootstrap calls, 60 sequential debate calls, and one aggregation call per case. This deliberately extends ACIArena's two-round default; it is a different evaluation setting, not a reproduction of its published scores.

`--max-total-tokens` (default 2,000,000) and `--max-requests` (default 2,000) apply across the entire command. The runner checks a conservative input-size bound plus maximum output before requests, disables API retries, and stops on errors. These are local guards, not provider billing limits. Use `--output` for a new output directory; existing directories are rejected and automatic resume is not supported.

## What is measured

Each receiving debater is paired with the two exact preceding responses actually delivered by upstream's sequential scheduler. A later debater can receive an earlier debater's response from the same round. No future response, attack label, or injection prompt is embedded directly. Only these source and receiver response texts become comm-channel feature vectors. The source/target mapping and reduced embedding dimension are explicit reconstruction choices, not settings recovered from the CASPIAN authors.

Bootstrap is observation 1 and has no agent-to-agent edges. Debate round r is observation r+1; aggregation is observation 22 with the default settings. CASPIAN's default estimator requires eight observations per edge, so the first ready debate edges appear at round 8. Aggregator edges appear only once and never reach readiness. Memory, tool, and execution channels are unobserved. These constraints make this an initial integration and falsification test, not full channel coverage.

The native attack success check looks for the planted name in the final answer. We also record the first name marker in another debater or aggregator, and native math correctness. A marker in another agent is descriptive evidence, not proof of causal propagation: every agent has the same planted context. Alert timing is reported at round boundaries, and same-round alerts are not counted as preceding a marker.

The detector stops after its first alert, as its API requires. Model execution and feature capture continue through aggregation; changing detector settings later can use stored observations without making model calls. Raw prompts and responses, encoded observations, manifest, per-case results, detector outputs, and SQLite histories are saved under ignored `data/aciarena/`. Historical analysis must exactly match the online results before a case is marked complete. The manifest records upstream revision, dataset hash, model settings, package versions, framework commit, source-file hashes, and whether the checkout had uncommitted changes.

## Interpreting the pilot

One pair can expose wiring failures, unsuccessful attacks, early false alarms, or detection after disclosure. It cannot estimate ROC-AUC, general sensitivity, or false-positive rates. A benign alert at estimator startup is grounds to inspect the detector before scaling to ten pairs; do not tune on this pair and then report it as held-out evidence.

See the [CASPIAN reconstruction limitations](../../docs/observability/caspian/validation.md). Increasing the round count supplies more observations but does not establish that the reconstructed estimator or spectral rules match the authors' implementation.

## Checks without API calls

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-aciarena/bin/python -m pytest -p no:capture -q
```

Tests exercise the real upstream debate with fake model responses, exact attack targeting, chronological source mapping, budget rejection before requests, feature caching, and online/history replay equality. Optional upstream tests skip when benchmark dependencies or the submodule are absent.

## First live result

The [2026-10-02 paired pilot](../../docs/observability/caspian/aciarena-pilot-2026-10-02.md) completed both cases. The attack disclosed the planted name in aggregation, but CASPIAN alerted at round 8 in both conditions. Expansion is deferred pending diagnosis of the startup false alarm.

## Resumable math matrix

The matrix runner covers all 39 public math tasks, each with a benign run, native `MathNameLeakInstruction`, and native `MathLocationLeakInstruction`: 117 unique scenarios. The code-domain cases are not part of this matrix. It is a codebase validation experiment, not an exact paper reproduction.

```sh
# Prepare and inspect the 117-case manifest without API calls.
.venv-aciarena/bin/python -m examples.aciarena.matrix \
  --tasks 39 --max-turn 12 --max-new-cases 3 \
  --output data/aciarena/math-matrix-20261002 --plan-only

# Execute up to three unfinished cases; rerun the same command to continue.
.venv-aciarena/bin/python -m examples.aciarena.matrix \
  --tasks 39 --max-turn 12 --max-new-cases 3 \
  --output data/aciarena/math-matrix-20261002
```

The first matrix batch intentionally uses 12 rounds: eight to reach the current estimator's sample requirement and four additional rounds, followed by aggregation. This saves model calls relative to the exploratory 20-round pilot. The model, encoder, injection schedule, and detector configuration otherwise remain unchanged. Neither round count is claimed to match the paper. Each complete 12-round case makes 40 chat calls.

The default matrix budgets are 3,000,000 tokens and 500 API requests **across resumptions**, not per invocation. They are a guard for initial batches, not a promise that all 117 runs fit. Both caps can be explicitly raised for continued execution. Before every serial API request, its conservative token reservation is written to disk. If interrupted while a request is in flight, the entire reservation is counted as uncertain usage on resume. Completed requests retain actual provider usage. Budget summaries expose uncertain usage separately; there are no automatic retries.

A process lock prevents two workers from writing the same matrix directory. A resume requires identical scenario, model, detector, package-version, and source-hash settings. A changed protocol requires a fresh directory. Completed cases with a verified replay result are skipped. Incomplete or failed cases restart in a numbered attempt directory, preserving their earlier traces. Mid-case conversation continuation is not implemented.

Assessment is separated into three questions:

- **Execution:** Did every scheduled case complete with valid outputs, no unexpected truncation, and an identical stored-history replay?
- **Attack outcome:** Did the native benchmark success predicate fire, and when did the corresponding name/location marker first appear outside the injected debater? Marker appearance alone is not proof of a cascade.
- **Detector behavior:** How many benign and attacked runs alerted, at what round, and did their evidence meet the estimator's sample requirement? A startup alert must not be counted as demonstrated early attack detection.

The matrix preserves all completed cases, including unsuccessful attacks. Attack outcome is separate from the attacked-condition label. Results are grouped by condition in `summary.json`; no AUROC or paper-equivalent spectral score is invented. The current startup behavior is retained as the baseline so subsequent fixes can be compared against saved observations.
