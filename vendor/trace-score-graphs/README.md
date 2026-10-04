# MAST → independent Jev score graphs

## Jev evidence audit

See [the scoring audit report](jev-scoring-audit-report.html). A frozen, purposive
audit of 14 focal records across seven trace formats compared fresh identical
requests with expanded history, using the same Jev model and all nine rubrics.
34 calls cost $0.007625. There were no >=0.5-level changes on identical-input
repeats, versus 19/53 jointly scorable measurements with added history.
Compatibility with 40 single-assistant reference ranges/statuses changed from
26 to 29 (five improvements, two regressions). This is diagnostic agreement,
not classifier accuracy or a representative estimate.

Expanded context corrected distant repetition scores but also inflated evidence
attributed to focal messages. Three alternative endpoints separately examine a
skipped tool exception and intermediate versus later task outcomes. They do not
share the same reference event or observation horizon.

**Earlier classification results require a cleaned rerun:** all 19 AppWorld
traces retain benchmark Evaluation trailers in 40 original scoring packets.
Audit focal inputs exclude them; original scores and numerical classifier
artifacts remain unchanged. Presentation reports now carry this warning.

The frozen protocol is `experiments/jev-evidence-audit-v1.json`; data and raw
responses are isolated in `data/scoring-audits/05d698d7be39d5bd`.

```sh
PYTHONPATH=src python3 -m trace_score_graphs.scoring_audit
PYTHONPATH=src python3 -m trace_score_graphs.scoring_audit --execute
```

The first command prepares requests only. The second uses content-addressed
caches when present. An exploratory 14-call explicit-focal-scope follow-up is
prepared but has not run; it requires approval after automatic review blocked
the additional outbound calls. Its prepare-only command is:

```sh
PYTHONPATH=src python3 -m trace_score_graphs.scoring_scope_audit data/scoring-audits/05d698d7be39d5bd
```

## Current experiments: corrected temporal event graphs (v2)

The actor-averaged experiments below are **superseded**. They preserved original
event data on disk but collapsed interactions between the same actors before
classification. They did not test the requested temporal event representation.

Use [event-experiment-report.html](event-experiment-report.html) for the complete
rerun. The original report entry points now show the corrected results; historical
HTML is retained with `-actor-projection-superseded` filenames, and all original
run directories are unchanged.

The corrected representation retains all 13,069 events and 10,331 unique scored
pairs in 300 conversations. It deduplicates 22 identical evidence records, keeps
unpaired events, joins interaction scores by exact evidence record ID, and carries
earlier source endpoints across window boundaries. Message attributes are not
projected onto another event's interaction. Directed links, event positions,
temporal-next relations, same-actor order, recipient metadata and score availability
are retained separately. The original source logs and Jev scores are unchanged.

Recipient metadata does **not** establish exact reply identity. All scored response
endpoints were originally selected by a heuristic. Three separate channels retain
1,613 recorded-recipient candidates, 2,561 workflow/two-party candidates and 6,157
unaddressed temporal candidates. None is promoted to an observed reply-to edge.
The benchmark's unrecorded communication cannot be reconstructed by classifier
training, and candidate edges are not evidence of causal influence or cascades.

For each channel, every event has an OUT and IN copy. Let C be the union of
chronological and within-window same-actor successor edges, T=C+Cᵀ, and S the
semantic matrix. A message attribute decorates S[i,i]; an interaction score
decorates S[source,target]. The symmetric lift is
`B=[[T,I+S],[I+S.T,T]]`, with signed degree `D=diag(sum(abs(B)))`, raw `L=D-B`
and normalized `N=D^-1/2 L D^-1/2`. The identity bridge joins two copies of the
same event and is explicitly a mathematical encoding, not a communication.
Chronology and signed semantic edges occupy different matrix entries, so a
negative agreement score cannot erase a temporal edge.

Classifiers consume **all raw and normalized matrix entries in window order**,
with sparse padding only. No actor pooling, spectral-only summaries, temporal
averaging, interpolation or truncation is used in model input. A separate topology
vector retains candidate edges even when numeric scores are missing. Observation
masks distinguish missing from measured-zero scores. Full graphs, matrices and
ordered window indexes are saved; average descriptors are used only for subsequent
descriptive class comparisons, never substituted for classifier inputs.

To fit the full variable-length representation, all methods now use the same
linear-kernel SVM (C=1, balanced classes). Kernels are exact dot products of sparse
full-matrix vectors. Centering and mean-diagonal scaling are fitted on training
rows only. Sigmoid decision scores are ranking scores, not calibrated probabilities.
This classifier and the corrected Laplacian encoding are methodological changes;
old-to-new performance differences do not isolate the aggregation correction.

The rerun includes activity, ordered scores, topology, scores+topology, all nine
individual layers, equal graph mixing, concatenated Laplacians, prediction averaging,
scores+Laplacians, and class-dependent mixtures. The same 65 fixed mixture candidates
use nested three-fold task-group selection. The previous outer five-fold task-group
and seven-framework splits, including shared-task purges, are unchanged. All 14
MAST labels are evaluated; any-problem versus no-issue is additionally evaluated
on 298 resolved traces. Full-data refit weights are separate from held-out metrics.

Reproduce the two complete training runs, audits and descriptive analysis:

```sh
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.event_experiments --window 20 --stride 10 --workers 2
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.event_experiments --window 10 --stride 5 --workers 2
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.event_audit data/event-experiments/44ba0b8be9e3a36a
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.event_audit data/event-experiments/d95fd3fc080ba25c
PYTHONPATH=src MPLCONFIGDIR=/tmp/swarm-lens-matplotlib OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.event_analysis data/event-experiments/44ba0b8be9e3a36a data/event-experiments/d95fd3fc080ba25c
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Run IDs depend on source hashes, code and dependency versions. The independent
audits verify event and score correspondence, every retained pair, all equal-graph
Laplacians by an independent edge-wise construction, selected full-vector dot
products, all 153,000 outer prediction cells per window size, and all 180 nested
weight selections per window size. There are 53 passing unit tests, including
one-to-many links, direction, temporal order, missingness and future exclusion.

Framework-held-out macro AP (20 / 10 events): scores-only 0.419 / 0.419;
topology-only 0.430 / 0.435; equal graphs 0.428 / 0.434; all Laplacians
0.428 / 0.433; learned mixtures 0.428 / 0.434. Learned-minus-equal intervals
include zero. The corrected experiment does not establish that semantic graph
weighting improves on topology controls. These are development results on a
selected cohort with overlapping labels and only 20 clean controls.

### Historical experiment documentation

The following sections retain the earlier protocol and results for provenance.
Their actor-projection classification commands and reports are not the current
temporal event experiment.


An isolated research experiment under `vendor/trace-score-graphs`. It includes
trace preprocessing, independent score layers and optional offline classification.
No application imports, routes, plugins, UI changes or CASPIAN integration.
Classification derives features without overwriting or merging the original scores.

## Storage contract

```
data/
  raw/                         pinned, checksum-verified original dataset
  inventory.json               availability and unknown-label counts
  duplicate-audit.json         identical traces and annotation disagreements
  selection.json               cohort assignment + complete original labels
  selected/<conversation>.json original selected records
  api-cache/<request-hash>.json exact request and original Jev response
  runs/<configuration-hash>/
    manifest.json              frozen rubric, extraction hash, evidence policy
    schemas/<conversation>.json normalized events with original source spans
    evidence/<conversation>.jsonl individual versioned evidence packets
    decisions/<record-id>.json  validated decisions and cache references
    scores/<score>/<conversation>.jsonl  ONE independent measurement stream per score
    graphs/<score>/<conversation>.json   ONE directed graph per score
    preprocessing.json         parsing coverage and limitations
    scoring.json               completed/failed requests for this invocation
    results.json               completeness, counts, model, total unique-call cost
```

All generated data is Git-ignored, including public source traces and API results.
The small code, schema specifications and experiment report may be committed.
Credentials are loaded from `OPENROUTER_API_KEY` or a specified `.env`; never copied
into outputs. No database is required. JSON/JSONL retains full score distributions,
confidence, applicability decisions, event positions, source spans and API provenance.
Raw traces and existing caches are not overwritten with transformed content.

## Nine independent scores

Message/node measurements: task relevance, novelty, instruction content,
verification evidence and harmfulness. Interaction/edge measurements: agreement,
acknowledgment, uptake and requirement fulfillment. Compliance mode is a separate
categorical stream. Rubrics are identical across all trace formats and cohorts.

Each numeric rubric has five ordinal levels. Store the raw expected level and
probability distribution; graph weights divide by four, except agreement which
maps to [-1, 1]. These are not calibrated probabilities of behavior. Unknown,
inapplicable, and failed decisions have `weight: null`, never zero. Raw scores are
retained even when the independent availability decision excludes them from a graph.
No scores are averaged together. No weighted agent-pair aggregation, threshold,
symmetrization or Laplacian is imposed. Event graphs preserve time, direction and
repeated interactions so later research can choose its own projections.

Every layer contains the same event nodes and unweighted chronological edges.
Message layers attach weights to nodes; interaction layers attach weights to
source/response edges. Chronology is not proof of receipt or causal influence.
An interaction with `temporal_candidate_unconfirmed_exposure` is explicitly only a
candidate relation. Two-party AG2 routing is an inference, not observed delivery.

## Reproduce

Python 3.11+; core pipeline uses the standard library only. From this directory:

```sh
PYTHONPATH=src python3 -m trace_score_graphs download
PYTHONPATH=src python3 -m trace_score_graphs sample --per-group 20 --seed 20261004 --include-disputed
PYTHONPATH=src python3 -m trace_score_graphs prepare
PYTHONPATH=src python3 -m trace_score_graphs score --env-file ../../.env --workers 8
PYTHONPATH=src python3 -m trace_score_graphs export
PYTHONPATH=src python3 -m trace_score_graphs audit
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Use `--data /path` before the subcommand to store data elsewhere. `score --limit 2`
runs a small smoke check; rerun without the limit to finish. Content-addressed
caching resumes successful calls. The default pinned model is
`typesafe/jev-1.13-20260917` through OpenRouter's `/api/alpha/decisions` endpoint.
No chat-completion fallback. Retry rate-limit rejections; report ambiguous transport
failures without silently fabricating scores. `--max-requests` and `--max-cost`
bound each invocation; parallel in-flight calls can finish after the spend stop.

## Extraction and evidence limitations

Seven deterministic adapters normalize ChatDev, MetaGPT, AG2, Magentic, AppWorld,
HyperAgent and OpenManus. The original trajectory remains the reference source.
Metadata, gold answers, annotation labels and cohort IDs never enter scoring state.
ChatDev summaries are not new messages; source prompts are retained when actually
recorded. AG2 Python-repr logs are read with `ast.literal_eval`, never executed.
Missing routes, abbreviated goals and unavailable images are recorded as limitations.
Some adapters cannot reconstruct hidden memories, tool implementations or deliveries.

Message questions see a focal event plus four earlier events. Interaction questions
add the first eligible recipient response within twelve global events (or the next
other-actor response as an explicitly unconfirmed candidate). The record's
`available_at` is the response position, not the original message position. Last
events with no response have message measurements but no invented interaction.

Evidence packets use deterministic UTF-8 head/tail budgets with retained-byte counts,
hashes and visible omission markers. Full original content stays in the schema.
This is a bounded-context instrument, not a claim that every decision sees the whole
conversation. Missing context can affect scores; coverage must accompany analysis.

## Data provenance

[MAST-Data](https://huggingface.co/datasets/mcemri/MAST-Data), revision
`95118ac951421753cf1deb87ddea3b01e693c41b`, CC-BY-4.0. Cite Cemri et al.,
*Why Do Multi-Agent LLM Systems Fail?* (NeurIPS 2025). Original file SHA-256:
`d636ac63dfc1c6af2d312e862f4b7d383b62d3898d431ccd0e7a79d21d85406f`.

The experiment samples conversations, not unique task questions. Repeated tasks
may appear as different executions and must be grouped for any later evaluation.
The 14 issue cohorts are multilabel, not mutually exclusive semantic categories.
The no-issue cohort means all 14 reference annotations equal zero; it is not a
claim that humans established the absence of every possible problem.

Exact UTF-8 conversation hashes remove duplicate conversations before allocation.
`--include-disputed` retains the single conversation and all its original annotations
when duplicates disagree: issue membership means at least one source annotation is
positive. Disagreement remains flagged; this is sampling, not label adjudication.
The no-issue group requires unanimous all-zero annotations across duplicate copies.
Without this option disputed conversations are excluded, which leaves fewer than
20 eligible examples of information withholding in the pinned dataset.

## Completed experiment

The completed run is `ad8a7238f9d58fff`. See [experiment-report.json](experiment-report.json)
for the cohort, results, limitations and artifact locations. Exact deduplication
reduced 1,642 records to 1,537 conversations. The selected 300 distinct conversations
contain 20 per cohort and include 22 with flagged annotation disagreements.

Jev scoring produced 2,700 graphs across nine independent layers, with 106,757
numeric measurement records. All 23,422 evidence packets completed; 14 unit tests
and the complete graph/evidence integrity audit passed. These checks establish
pipeline integrity, not classifier validity. Data and API caches remain local and
Git-ignored. Earlier run directories are superseded and retained for provenance.

## Windowed Laplacian classification

The comparison is complete. See [classification-report.html](classification-report.html)
for both window sizes and links to every per-label result, or
[classification-report.json](classification-report.json) for the machine-readable summary.

The original nine score streams remain unchanged. Analysis adds actor graphs on
fully contained source–response pairs in trailing windows of 20 events / step 10,
with 10 events / step 5 as a sensitivity comparison. Events include tools and
artifacts, not only chat messages. Short traces receive one partial window.
Five message scores decorate the source of each paired interaction; the four
interaction scores decorate that same pair. Unpaired messages are excluded from
both graph and score baselines. An edge is the mean observed score for that actor
pair. All layers use the same actors in each window. The average graph averages
observed layer adjacencies before computing its Laplacian. Observation masks and
counts distinguish missing scores from measured zeros. Agreement stays signed.

For a directed adjacency A, use the symmetric bipartite lift B = [[0,A],[A.T,0]],
absolute degree D = diag(sum(abs(B))), combinatorial L = D-B, and normalized
L = D^-1/2 (D-B) D^-1/2. Isolated vertices have zero inverse degree. This follows
the [absolute-degree signed Laplacian construction](https://arxiv.org/abs/1601.04692).
The lift preserves directed adjacency in the saved matrix, but its spectrum is
not a complete invariant of direction or signs. Normalized spectral descriptors
are accompanied by combinatorial descriptors so uniform weight scale is not lost.
Descriptors are summarized over time, rather than feeding variable-size matrices
directly into a neural network. The frozen protocol records every descriptor.

Models: nine individual layers, average graph, all Laplacians, score-only,
scores plus Laplacians, activity-only and a training-prevalence baseline. An
equal-weight average of individual-model predictions is also evaluated. Every
learned model uses fixed C=0.1, L2, class-balanced logistic regression, with
imputation and scaling fitted only on training examples. Original 14 multilabel
annotations are targets; cohort assignments never enter features or targets.
129 disputed/unknown annotation cells are masked, leaving 4,071 known cells.

Five-fold task-group evaluation and leave-one-framework-out evaluation share
the same held-out conversations across methods. Framework-held-out training
also excludes exact normalized task matches from the test framework. Task
identity cannot always be recovered from abbreviated or differently worded logs.
All windows from a conversation remain together. AP denotes non-interpolated
average precision, not accuracy or trapezoidal PR-AUC. Per-label AP, ROC-AUC,
precision, recall and F1, macro metrics, and conditional paired bootstrap
intervals are saved. F1 uses a fixed 0.5 threshold; outputs are not calibrated.

Install the optional analysis dependencies and run from this directory:

```sh
python3 -m pip install -e '.[analysis]'
PYTHONPATH=src python3 -m trace_score_graphs.classification --window 20 --stride 10
PYTHONPATH=src python3 -m trace_score_graphs.classification --window 10 --stride 5
PYTHONPATH=src python3 -m trace_score_graphs.classification_audit data/classification/68d5eb7653a9641c
PYTHONPATH=src python3 -m trace_score_graphs.classification_audit data/classification/eab6e9778df715d4
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Experiment IDs depend on code, source hashes, configuration and dependency
versions, so a different installation may produce a different directory ID.
Each `data/classification/<id>/` contains the pre-fit protocol, source hashes,
window descriptions, adjacency/observation/Laplacian arrays (`matrices/*.npz`),
conversation feature matrices, saved fold parameters, task/framework splits,
held-out predictions, per-label CSV, full JSON, PNG and HTML reports, and audit.
Both completed runs passed all artifact checks; 23 unit tests passed.

Observed macro AP (task-group / framework-held-out), 20-event windows:
scores-only 0.578 / 0.412; average graph 0.556 / 0.433; all Laplacians
0.560 / 0.423; activity-only 0.559 / 0.436. Requirement fulfillment was the
strongest individual layer on held-out frameworks (0.441). At 10 events,
the corresponding values were 0.571 / 0.400, 0.553 / 0.446, 0.562 / 0.420,
0.559 / 0.447; requirement fulfillment reached 0.458. These are exploratory
reference-label predictions. The current experiment has not demonstrated a
consistent advantage attributable to combining Laplacians.

## Class-dependent graph mixtures

The next experiment learns a different nine-layer adjacency mixture for each
MAST class before calculating its Laplacian sequence. See
[weighted-classification-report.html](weighted-classification-report.html) or
[weighted-classification-report.json](weighted-classification-report.json).

For each actor pair, the numerator is the sum of alpha[d] × observed[d] × A[d]
and the denominator is the sum of alpha[d] × observed[d]. With no available
positive-weight layer the edge remains unobserved/zero in the matrix. Agreement
retains its sign. Each class's alpha is nonnegative and sums to one; the same
alpha applies to all time slices. Laplacian construction and 126 conversation
features match the previous average-graph variant, including the unchanged
per-layer availability features. Uniform weights exactly reproduce that baseline.

Learning is a finite constrained search: 65 label-independent candidates contain
the uniform mixture, layer-directed moves at strengths .25/.5/.75/1, and 28 seeded
Dirichlet mixtures. Three inner task-group folds score each candidate by pooled
held-out AP. The objective subtracts 0.05 times normalized squared distance from
uniform. Classifier C=0.1 and all other settings remain fixed. The search and
every imputer/scaler are fitted inside the outer training set. Existing task-group
and framework-held-out splits, including task-overlap purging, are unchanged.
This uses [nested validation](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html).
It is not a claim of globally optimal weights or end-to-end gradient training.

After evaluation, each class is independently refitted using all 300 conversations
with the same inner procedure. Its weights and classifier parameters are saved
separately in `final-models/`. These full-data models are not used to calculate
held-out performance. Per-fold weight variation is available in CSV. Large weights
are model parameters, not causal attributions or calibrated contribution shares.

```sh
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.weighted_classification data/classification/68d5eb7653a9641c --workers 4
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.weighted_classification data/classification/eab6e9778df715d4 --workers 4
PYTHONPATH=src python3 -m trace_score_graphs.weighted_audit data/weighted-classification/4a1aa10569287700 data/classification/68d5eb7653a9641c
PYTHONPATH=src python3 -m trace_score_graphs.weighted_audit data/weighted-classification/573b62a4c8b91197 data/classification/eab6e9778df715d4
```

Run IDs hash code, source artifacts, versions and configuration. Each weighted
run stores the frozen protocol, source hashes, all candidate feature matrices,
inner split assignments and candidate predictions, class selections, trained
models, outer held-out predictions, per-class metrics, paired uncertainty
estimates, final refit weights, reports and audit. Combined matrices can be
reconstructed from the saved original layer matrices and selected alpha.
31 tests passed. Audits reconstructed all outer predictions, verified every
inner candidate score and selection, and checked selected feature calculations
against the scalar Laplacian implementation on 30 fixed conversations per run.

This is an exploratory follow-up on previously inspected outer test sets.
Bootstrap intervals condition on fitted folds and do not include retraining.
The finite candidate bank and all limitations of the source annotations, evidence
windows, graph projection and spectral summaries remain relevant.

## Class-by-class Laplacian contrasts

[class-contrast-report.html](class-contrast-report.html) compares all nine score
layers, the equal graph, and the fourteen class-specific mixtures on the saved
300 conversations. It includes an interpretation for every MAST class, contrasts
with clean traces and with other problems, temporal changes, and a matrix audit.
No new scoring calls or classifier fits are performed.

The reference groups are 278 conversations with at least one consensus issue,
20 with all fourteen labels absent, and 2 unresolved conversations excluded from
the overall binary contrast. Individual-class comparisons mask unknown label
cells. Class labels overlap; cohort assignments are never substituted for labels.
Window descriptors are averaged per conversation. Original source-score means,
availability, all nine spectral descriptors, and actor-aligned normalized-matrix
drift remain separate measurements. Drift is unavailable for one-window traces.

Each class mixture uses the saved weights trained without that conversation's
entire framework, with exact-task matches also purged. Full-data refit weights
are excluded. Every original Laplacian is recomputed, and all class-mixture
spectral means are checked against the previously audited feature bank.

Effects are Cliff's delta, both pooled and averaged equally within frameworks.
A sensitivity comparison uses equally weighted framework × event-count strata
(1–9, 10–19, 20–49, 50–99, 100+), retaining only strata with both groups. The
comparison therefore changes its overlap population and cannot fully remove
length/task confounding. Descriptive 95% Bayesian-bootstrap intervals use 400
exponential weights on exact-task clusters, conditional on the observed cohort
and saved weights. They are not corrected for multiple comparisons and are not
claims of statistical significance. Primary windows contain 20 events; the
10-event analysis is a sensitivity check.

```sh
PYTHONPATH=src MPLCONFIGDIR=/tmp/swarm-lens-matplotlib OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m trace_score_graphs.class_contrasts
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Each `data/class-contrasts/<id>/` saves the protocol and source hashes, original
and cross-fitted measurement arrays, indexes, all numeric contrasts as JSON/CSV,
window-level matrix diagnostics, cross-fitted weights, figures and an integrity
audit. `class-contrast-report.json` identifies the current runs. Numeric scores
and earlier graph/classification artifacts remain unchanged. The HTML embeds its
figures so it can be viewed independently; linked raw artifacts remain local.

The strongest problem-versus-clean raw-intensity contrast is requirement
fulfillment (within-framework delta −0.54 at 20 events; framework × length-bin
delta −0.53), reproduced at 10 events. Several other apparent differences weaken
after the length comparison. Repetition and termination-recognition failures
show clearer profiles than verification failures. Normalized spectra erase many
layer differences: 53.4% of both-nonempty layer pairs have identical spectra at
20 events and 67.8% at 10. The equal graph's second normalized eigenvalue is zero
in every nonempty window of this bipartite lift. These findings limit which
spectral measurements are informative; they do not validate a general detector.

The independent effect-size audit can be rerun with
`PYTHONPATH=src python3 -m trace_score_graphs.class_contrast_audit`. It compares
every pooled, within-framework and framework × length-bin delta against SciPy
Mann–Whitney U statistics and verifies all saved source hashes.
