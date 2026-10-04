# CASPIAN in Swarm Lens

This is an independent, executable reconstruction of the method specified in [CASPIAN, arXiv:2605.19240v1](https://arxiv.org/abs/2605.19240v1), **Online Detection and Attribution of Cascade Attacks in LLM Multi-Agent Systems via Cross-Channel Causal Monitoring**, by Kavana Venkatesh, Jafar Isbarov, Saad Amin, Murat Kantarcioglu, and Jiaming Cui (May 2026).

It implements the full specified pipeline: channel-event aggregation, past-only target history, streaming Gaussian-copula conditional dependence, channel influence tensors, topology normalization, spectral signals, instant/adaptive persistence decisions, and interval-based role/spine attribution. It is **not an exact reproduction of the authors' unreleased implementation or published accuracy/latency results**. The paper omits essential estimator/encoding parameters and contains contradictory or degenerate rules. Those are exposed rather than silently repaired. Read [coverage and ambiguities](coverage.md) before interpreting alerts.

## Install and verify

From the repository root, Python 3.11+:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[caspian,dev]'
python -m pytest tests/observability/caspian -q
```

The retired standalone demo CLIs are no longer onboarding examples. Numeric branch/estimator fixtures remain under `tests/support/`. Without an editable install, use `PYTHONPATH=src` from the repository root. The supplied Conda runtime requires:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:capture -q
```

NumPy is the only method runtime dependency. The core remains dependency-free. No model SDK, remote service, credentials, or GPU is required for these tests.

## Input contract

```python
from swarm_lens.observability.caspian import Caspian, ChannelEvent, Turn

monitor = Caspian(
    agents=("sender", "receiver"),
    edges=(("sender", "receiver"),),
    feature_schema="my-encoder-v1/runtime-features-v2",
    observed_channels=("exec",),
)
result = monitor.update(Turn(1, (
    ChannelEvent("sender", "receiver", "exec", (12.0, 0.0), (18.0, 0.0)),
)))
```

The sample vectors above are illustrative observed numeric features, not a recommended feature schema. Your application supplies:

- A fixed, ordered set of agent IDs and directed structural possibility edges. Self-edges and unknown endpoints are rejected. Edges represent possible influence, not observed influence weights. To model topology changes, define a new analysis segment with explicit provenance.
- Contiguous turns numbered from 1, including empty turns. A turn is an application-defined interaction round, not automatically an event, timestamp, or model token.
- `ChannelEvent(source, target, channel, source_vector, target_vector)`. Channels are exactly `comm`, `mem`, `tool`, `exec`, in that output order. Vectors must be nonempty and finite. Source and target dimensions may differ; their dimensions must remain fixed within each channel.
- Independently constructed source features and **actual downstream target behavior**. Never duplicate a sender's payload as the target vector. Do not use future responses beyond the current turn/history prefix. Aggregate exposure/response pairs when the response becomes observable.
- A versioned feature/encoder schema. Text channels require application-supplied embeddings and metadata; execution can use measured numeric features. The paper specifies no embedding model, dimension, or projection weights. The optional OpenAI adapter below uses the user-selected encoder with explicitly documented dimensions.
- Declared observed channels. Absent channels remain zero, with entropy divided by `log(4)` as in the paper. This is a partial-observation setting, not equivalent to four-channel validation.

Expand broadcasts into directed pairs only for recipients known to be exposed, and score them only once downstream target features exist. Memory/tool inputs require artifact lineage from a producing agent to an observing consumer. Empty observations mean no new evidence, not evidence of no influence. Events outside the topology are counted as masked and excluded from both scoring and target histories.

## What is computed

Within each turn, events are averaged per directed edge/channel. Target EMA histories are averaged across individual incoming events, updated **after all edges are scored**, and initialized at zero. Every edge/channel maintains its own online marginal ranks and exponentially weighted covariance over `[source, target, previous_target_history]`.

The Gaussian CMI implementation conditions both source and target covariance blocks on history using a Schur complement, then computes half the log determinant ratio in nats. Shrinkage toward the diagonal and positive diagonal jitter stabilize the covariance. Scores are clipped at zero. Equations (20)-(22)'s residual narrative is implemented through the Gaussian conditional covariance; merely correlating the source with a target residual is not generally equivalent to conditional mutual information.

Raw channel matrices are masked and summed for bridge attribution. With the default `raw_sum` policy, their sum receives the paper's degree normalization `A[i,j] / (sqrt(out[i] * in[j]) + epsilon)`. Individual channel matrices receive the same degree normalization for entropy and dominant-channel attribution. `positive_zscore` is an explicitly experimental interpretation of Figure 1: population z-scores across feasible edges per turn/channel, clipped at zero, then the same aggregation/degree normalization. The paper does not specify this clipping or z-score domain; neither policy is claimed to reproduce the authors' hidden normalization.

SVD supplies the two leading singular values, not eigenvalues. The monitor computes energy growth, coupling ratio, gap/contraction, phase change, and normalized channel entropy. It applies equations (7)-(10), caches the active candidate interval, and latches the first alert using Algorithm 1. Measurement continues through the end of the input stream, including turns after confirmation. The first turn establishes a spectral baseline; it cannot WATCH. An alert does not establish that an attack occurred.

Attribution ranks origin by onset outflow, amplifier by summed per-turn outflow/inflow ratios, and bridge by summed raw outflow-times-inflow products. Spines are exact simple directed paths bounded by structural diameter and ranked by interval-maximum bottleneck. Dominant channels use sums of interval-maximum normalized channel entries. Agent/channel ties follow input order; spine ties use lexicographic agent-index paths. Reported paths describe an envelope of influence, not necessarily a time-respecting trajectory: maxima can occur at different turns.

## Configuration and reconstruction choices

Pass `CaspianConfig(...)`. Resolved values and topology are included in `describe()` and persisted plugin results. Only the four channel names, entropy threshold 0.5, and mathematical rules are specified by the paper; these numeric estimator defaults are implementation choices, not author settings.

| Setting | Default | Meaning |
| --- | --- | --- |
| `history_alpha` | 0.1 | New observation weight in every channel's target EMA |
| `covariance_alpha` | 0.05 | New sample weight in edge/channel covariance |
| `shrinkage` | 0.05 | Diagonal covariance shrinkage |
| `jitter` | 1e-8 | Covariance diagonal stabilization |
| `epsilon` | 1e-8 | Spectral/degree/ratio denominator stabilization |
| `min_samples` | 8 | Distinct observed turns per edge/channel before nonzero estimation |
| `entropy_threshold` | 0.5 | Equation (6) threshold; Appendix A studies alternatives |
| `top_k` | 3 | Maximum spines returned |
| `normalization` | `raw_sum` | Appendix C sum interpretation; alternative `positive_zscore` is experimental |
| `persistence` | `algorithm1` | Retain candidate through WATCH drops and test majority at deadline; alternative `reset_on_watch_drop` follows Section 4.3.2 prose |
| `missing_evidence` | `hold` | Retain last edge estimate; `zero` clears inactive entries each turn; neither adds samples |
| `path_budget` | 100000 | Maximum simple paths enumerated before marking attribution unavailable |

Online ranks use the inclusive prefix empirical midrank `(number_less + (number_equal_including_current)/2) / prefix_size`, transformed by the standard normal inverse CDF. Equal values at the first observation map to zero; ranks are exact, not a sketch. Historical transformed samples are not reranked. Covariance starts at the first transformed sample with zero covariance; subsequent updates use `mu += alpha*delta`, `C = (1-alpha)*(C + alpha*delta*delta.T)`. The current sample enters covariance before its score is read. The paper does not specify these choices or update timing precisely.

## Outputs and framework integration

`update(Turn)` returns a JSON-compatible dictionary containing turn number, influence tensor `[source][target][channel]`, raw and normalized matrices, sample counts/readiness, masked-event count, all spectral signals, candidate onset/deadline, an optional latched first alert, and `new_alert` (true only on the confirmation turn). An alert includes classification, onset/confirmation turns, attribution status, role rankings/scores, and spines/channel scores. Results own their numeric arrays converted to lists. `has_detected` becomes true on the first alert; `finished` stays false because measurement ends with the input stream. Later updates continue estimator histories, sample counts, tensors and spectral signals, while preserving the original detection and attribution. No repeated-alert or recovery policy is inferred. Returned alerts are independent copies. This continuous-measurement behavior is versioned as CASPIAN 0.2.0; prior results remain historical 0.1.0 analyses.

`ObservabilityPlugin` supplies the standard integration without modifying `core/` or `application/`. The [branch regression fixture](../../../tests/support/caspian_branch.py) records application observations as `observation.recorded`, analyzes a historical prefix, forks, records a prompt intervention, makes a nested fork, and verifies equivalent observation histories give identical scores. Each analysis reconstructs a fresh monitor, so parent future events and another branch's estimator history cannot leak into the selected branch.

The library does not automatically infer observations from standard framework entities. A recorded prompt edit cannot supply new target behavior. A runtime must execute a continuation and emit the required evidence before scores can change for that intervention. For a long-lived online runtime, call `update` once per completed observed turn and persist your observations; deterministic replay provides restart semantics.

## Regression fixtures and observed limitations

The [synthetic fixture](../../../tests/support/caspian_synthetic.py) changes seeded independent numeric pairs to strongly dependent pairs at turn 60. **Default rules alert at turn 8, when estimates leave warmup, before the intended shift.** This remains an explicit limitation covered by regression tests, not a successful detection benchmark. The [branch fixture](../../../tests/support/caspian_branch.py) tests historical and nested-branch isolation against real SQLite storage. Direct attribution calculations are covered in `tests/observability/caspian/test_topology.py`.

The old AI Village schema-audit CLI was retired with its gated downloader. The [current public-recording mapping](../../ai-village.md) still does not establish verified recipient exposure, cross-agent tool use, or private-memory lineage. No CASPIAN pairs or cascade labels are inferred from those missing records.

The literal weak-link criterion is automatically satisfied on a nonempty topology because one-edge paths are allowed. Phase shift is nearly implied by positive gap contraction. Degree normalization makes the leading singular value approximately 1, so WATCH's strict leading-value growth can be driven by epsilon and floating-point effects. Channel degree normalization similarly makes entropy mostly reflect the number of active channels. These are concrete mathematical limitations of the published specification; see derivations in [coverage.md](coverage.md).

Dense covariance/Schur solves scale cubically in compact vector dimension; dense SVD scales cubically in agent count. Exact prefix ranks retain all samples and use linear-time insertion. Exact simple-path enumeration can be exponential; it returns no approximate spines when its explicit budget is exceeded. In that case detection remains recorded with `attribution_status="path_budget_exceeded"` and no attribution object. Candidate cache size grows with the adaptive window; there is no silent cap even near zero gap. This implementation makes no sub-1% overhead or large-team scalability claim.

## Validation scope

Tests compare streaming covariance to independently weighted batch covariance; CMI to analytic Gaussian examples and a separate determinant identity; directed singular values to hand calculations; role scores to hand calculations; and path enumeration to brute-force permutations. They cover no-event turns, ties, singular covariance, unknown/invalid inputs, channel masking, history timing, both persistence interpretations, exact interval endpoints, numerical degeneracies, first-alert preservation during continuous measurement, path-budget reporting, deterministic replay, nested forks, and persisted provenance. No published dataset examples or author numeric fixtures were released to compare against.

Published TAMAS/ACIArena experiments, A100 timings, model comparisons, ablation accuracy, confidence intervals, and AUROC/TPR scores have **not** been reproduced. They require missing execution traces, encoder/settings, author code, and costly model/framework runs. Appendix E describes a scalar spectral score without giving its formula; we do not invent a benchmark score to claim parity.

## OpenAI text embeddings

The optional outer adapter `swarm_lens.adapters.openai_embeddings.OpenAITextEncoder` now uses **text-embedding-3-small**, selected for this project. The numerical CASPIAN package remains provider-independent. This encoder selection does not recover the paper's unpublished encoder.

```sh
python -m pip install -e '.[caspian,embeddings,dev]'
# For a new checkout: copy .env.example to .env and populate OPENAI_API_KEY.
# Preserve an existing local .env. No model calls are made by the tests.
python -m pytest tests/observability/caspian/test_embeddings.py -q
```

Applications can call `OpenAITextEncoder.from_env().encode(texts)` when real embeddings are required. That makes a provider request. Unit tests mock the API and never load local credentials; the old two-text API smoke CLI was removed.

`OpenAITextEncoder.from_env()` loads the explicitly named `.env` (default: current directory), preserving existing process environment variables. `CASPIAN_EMBEDDING_MODEL` must be `text-embedding-3-small`. `CASPIAN_EMBEDDING_DIMENSIONS` defaults to **16**, requested through the API's `dimensions` parameter to keep the streaming covariance compact. This is a configurable experimental choice, not a paper setting or a claim of statistical adequacy. Larger embeddings require more data and more expensive covariance calculations; changing dimensions creates a different feature schema and requires a fresh monitor.

`encode(texts)` batches inputs, restores input order using response indices, validates dimensions and finite values, and returns tuples. It neither truncates nor changes text; overlong inputs remain provider errors. `feature_schema` records model, dimensions, and preprocessing for `Caspian(..., feature_schema=encoder.feature_schema)`. Applications still own observed source/target pairing and metadata features. No text is sent until `encode` is called. See the [OpenAI embedding guide](https://developers.openai.com/api/docs/guides/embeddings).

`.env` is ignored by Git. `.env.example` is the explicit placeholder-only exception and contains no copied secret values.
