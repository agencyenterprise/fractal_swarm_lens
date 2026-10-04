# Paper coverage and reproducibility record

## Sources inspected

- [Complete paper, v1](https://arxiv.org/pdf/2605.19240v1), 28 PDF pages: main text, references, and appendices A-E. Main-text page labels run 1-18; appendix labels restart at 1. Title/authors match the paper-linked repository. The arXiv identifier dates to May 2026; the PDF header says May 20 and arXiv v1 stamp says May 19.
- [Authors' repository](https://github.com/caspian-detector/caspian/tree/acee8e48ea55aeb0ee892d6a061c0e592a4233fc), inspected at commit `acee8e48ea55aeb0ee892d6a061c0e592a4233fc`. Its tracked tree has `LICENSE`, `README.md`, `images/caspian-method.png`, and `images/dummy.png`. The README mentions missing experiment/evaluation modules, requirements, configs, and placeholder benchmark links. It is not executable upstream code. No code was imported, so no submodule is needed.
- Downloaded PDF SHA-256: `9ee94ba25f34ef69a74906d48ba913acf523c89b871ba8e4da56da851c210002`. Reference downloads are outside the implementation checkout and are not redistributed.

This reconstruction starts from Swarm Lens `4b93f0e13570a621b6f46e70c79db95e75a006d0`. It does not alter the original demo checkout/server.

## Coverage map

Implementation paths below are relative to `src/swarm_lens/observability/caspian/`; test paths are relative to `tests/observability/caspian/`. The input mapping is deliberately an application responsibility.

| Paper component | Implementation | Verification / status |
| --- | --- | --- |
| Section 4.1 agent set, four channels, structural graph | `inputs.py`, `config.py`, `topology.py:topology` | `test_integration.py:test_turn_order_invalid_input_and_declared_observation_contract`; mask tests in `test_estimator.py` |
| Appendix C/Table 8; Eq. (15), normalized directed events, broadcast expansion | `ChannelEvent`, `Turn`; application `HistoryAdapter` | `examples/observability/caspian/branch_plugin.py`; `test_integration.py`; verified source/target pairing and encoders must be supplied, not inferred |
| Eqs. (16)-(17), triplet grouping and within-turn means | `estimator.py:InfluenceEstimator.update` | `test_triplet_averaging_target_history_event_weighting_and_no_leakage`, `test_event_order_independence_masking_and_return_isolation` |
| Eq. (18), target-specific EMA, zero initialization, scoring before history update | `InfluenceEstimator.update` | Same hand-computed history test, including two incoming sources and event-weighted means |
| Eq. (1), Eq. (19), LI-CTE | `EdgeEstimate`, `gaussian_cmi` | Analytic correlated Gaussian, conditionally independent common-history case, multivariate determinant reference |
| Eqs. (20)-(22), target prediction/residual dependence narrative | `gaussian_cmi` Schur complement of history | `test_gaussian_cmi_closed_form_and_history_confounder`, `test_multivariate_cmi_independent_reference_logdet_identity`; both source and target conditioned as required by Gaussian CMI |
| Appendix C step (6), marginal rank transform | `OnlineRanks` | `test_online_ranks_midties_prefix_only_and_monotone_invariance`; exact inclusive online ranks are a documented choice |
| Appendix C step (6), EW covariance, shrinkage, jitter, nonnegative score | `EWCovariance`, `gaussian_cmi` | `test_streaming_covariance_equals_batch_exponential_weights`, `test_singular_features_stay_finite_and_nonnegative`, `test_streaming_dependence_exceeds_independent_control` |
| No new events on triplet | `InfluenceEstimator.update`, `missing_evidence` | `test_missing_events_do_not_add_samples_or_update_history`, hold/zero policies; paper does not settle matrix retention |
| Eq. (2), Eq. (23), tensor | `InfluenceEstimator.tensor` | Shape/ordering/masking and integration tests |
| Eq. (24), raw channel sum | `topology.py:make_snapshot` | `test_degree_formula_mask_raw_and_channel_slices` |
| Figure 1 channel z-normalization; Section 4.2 normalization | `make_snapshot`, `degree_normalize`, named normalization policies | Exact degree equation tested; channel z-normalization is incomplete in paper. `positive_zscore` is explicitly experimental, not asserted as recovered author code |
| Eq. (3), spectral energy/amplification | `detection.py:measure` | `test_signals_hand_computed_singular_values_entropy_and_watch`; nilpotent directed matrix distinguishes SVD from eigenvalues |
| Eq. (4), ratio/gap/contraction | `measure` | Same independent numerical test |
| Eq. (5), phase magnitude and rule | `measure` | Hand calculation and `test_phase_rule_is_almost_implied_by_positive_contraction` |
| Eq. (6), per-channel energy, share, entropy | `measure` | Uniform four-channel, zero-channel, single-channel tests; fixed log(4) denominator |
| Eq. (7), WATCH conjunction | `measure` | Hand calculation, startup baseline, zero/single-agent cases |
| Eq. (8), widest-path bottleneck and energy-weighted scale | `topology.py:weak_link` | `test_weak_link_literal_rule_equals_maximum_edge_and_is_vacuous`; exact algebraic simplification for stated path set |
| Eq. (9), instant confirmation | `CascadeDetector.advance` | `test_instant_first_alert_and_stop` |
| Eq. (10), adaptive window/majority/transition | `CascadeDetector.advance` | `test_algorithm1_majority_inclusive_window_and_interval_attribution`, `test_unconfirmed_candidate_resets`, `test_huge_adaptive_window_is_not_silently_capped` |
| Section 4.3.2 WATCH-drop prose | `persistence='reset_on_watch_drop'` | `test_prose_reset_policy_discards_on_watch_drop`; differs from Algorithm 1 default |
| Eq. (11), origin | `topology.py:attribute` | `test_roles_interval_maxima_and_dominant_channel_reference` |
| Eq. (12), amplifier (sum of ratios, not ratio of sums) | `attribute` | Same interval test; complete ranking/scores returned |
| Eq. (13), bridge using raw matrices | `attribute` | Same test independently computes raw scores 60 and 258 |
| Eq. (14), top-K simple spines, interval maxima | `top_spines`, `attribute` | Brute-force permutation oracle, exact tie ordering, length/diameter restriction, interval envelope test |
| Section 4.4 dominant channel and output | `attribute` | Two-snapshot comm/tool example; all channel scores retained |
| Appendix D/Algorithm 1 first decision; continuous measurement | `method.py:Caspian`, `CascadeDetector` | Instant/persistence, immutable first-alert result with measurements continuing to end of input, cursor replay, fresh branch state, persisted output tests. Cache retains only active candidate since earlier matrices cannot enter attribution |
| Appendix A ablations, Table 5 thresholds/windows | Configurable entropy and normalization; default adaptive window | Published ablation results not reproduced; no invented fixed-window or ablation accuracy claims |
| Appendix B latency/complexity | Dense NumPy implementation, exact rank storage, explicit path budget | No hardware/overhead parity claimed; actual complexity differs, explained below |
| Appendix E benchmark scenarios, labels, metrics, bootstrap | No benchmark executor or fabricated labels | Missing author traces/encoders/scalar-score formula prevent exact replication; not a missing online algorithm stage |
| Swarm Lens history/branch/plugin architecture | `observability/plugin.py`, application example | `test_integration.py`: historical cursor, parent future exclusion, nested fork, restart persistence, second method protocol |
| AI Village applicability | `examples/ai_village/caspian.py` | `test_village.py`: no guessed pairs or payload leakage; schema audit only |

Rule-level persistence tests inject controlled `Signals` to exercise the state machine independently. They do **not** claim that those combinations occur in the literal normalized pipeline. End-to-end tests use actual covariance/SVD computation separately.

## Ambiguities and consequences

### Missing encoder and estimator settings

No embedding checkpoint, dimensionality, payload-to-source/target pairing recipe, rank estimator, rank window, EMA rate, covariance shrinkage, jitter, warmup, or sample timing is supplied. Appendix C describes a Gaussian-copula covariance approximation, not a unique executable estimator. Our defaults and equations are fully listed in the README. They implement that estimator family but cannot establish numerical parity with the authors. Plain text alone is not accepted as a substitute for the required observed source/target vectors.

### Channel normalization is not specified consistently

Figure 1 labels channel z-normalization, Section 4.2 says normalize and aggregate, and Appendix C explicitly sums raw channel scores before describing topology normalization. There is no z-score formula, reference population, temporal window, channel weighting, or rule reconciling negative z-scores with a nonnegative influence matrix. We supply `raw_sum` as an Appendix-C-based interpretation and an explicitly named `positive_zscore` alternative. Choosing either is a disclosed assumption; exact channel normalization remains unrecoverable. The same ambiguity affects whether dominant-channel maxima should use raw or normalized slices; this implementation uses normalized slices, matching the tilde notation around attribution.

### Degree normalization constrains the main signal

For a nonzero nonnegative matrix A, set r=A1 and c=A^T1. With epsilon=0 and ignoring zero-degree rows/columns, B=diag(r)^(-1/2) A diag(c)^(-1/2) satisfies B sqrt(c)=sqrt(r), and B^T sqrt(r)=sqrt(c). A weighted Cauchy-Schwarz bound gives operator norm at most 1, so the leading singular value is exactly 1. This is an independent mathematical consequence, not a claim made by the paper.

Thus the published WATCH condition's strict growth of the leading singular value is not a robust independent amplification measure under this normalization. Positive epsilon makes it approximately 1 with scale-dependent changes; rounding can also affect strict comparisons. Each separately normalized active channel likewise has leading value near 1, so entropy largely counts active channels. We preserve the formulas, expose the measurements, and test these consequences instead of switching to unnormalized singular values without disclosure.

### Weak-link path family makes the rule vacuous

The stated family includes one-edge simple paths and does not require particular endpoints or a minimum hop count. Every longer path's bottleneck is bounded by its largest edge, and the strongest single edge is itself an allowed path. Therefore B=max(w). For nonnegative weights, sum(w^2)/(sum(w)+epsilon) <= max(w). Consequently Eq. (8) always holds for a nonempty edge set, even all-zero weights; WATCH prevents the all-zero case from alerting. The implementation returns false for an edgeless graph, since there is no nonempty path.

Requiring two hops, origin-to-sink endpoints, positive bottlenecks, or a percentile threshold would change the method; none is silently introduced. Exact top-K spines still enumerate the full permitted path set. This can favor one-edge paths over an intuitive cascade narrative.

### Phase shift and persistence

When coupling increases by d>0, gap contraction is d while phase magnitude is d/(R_previous+epsilon). Whenever R_previous+epsilon<1, the phase predicate is already true. Combined with the weak-link degeneracy, typical WATCH onsets therefore immediately alert, leaving little scope for the multi-turn behavior claimed in the paper.

Section 4.3.2 says discard on WATCH loss; Algorithm 1 instead checks majority WATCH at the deadline and only then discards a failed interval. The default follows Algorithm 1. The prose alternative is available and tested. No arbitrary phase threshold or suppression of startup alarms is added to force realistic-looking experimental results.

### Startup and nearly zero gap

Initial spectral values are not specified. We establish the first turn as a baseline, produce zero influence before `min_samples`, and do not add a second undocumented detection warmup. A rise out of estimator warmup can trigger an alert on independent samples; the runnable default synthetic example exhibits this at turn 8. Tiny gaps imply very long adaptive windows (100 million turns at zero gap with epsilon=1e-8). No silent window cap is applied. Applications must budget memory or explicitly choose a different, separately documented method variant.

### Paths, ties, timing and topology

Directed diameter is undefined/infinite on many disconnected graphs. We use the largest finite directed shortest-path distance, and zero for edgeless graphs. Path length counts edges; paths are simple and nonempty. Tie rules are deterministic and documented, but the authors give none. The topology is fixed during a monitor; runtime agent/edge changes require a new explicitly defined segment. Elementwise temporal maxima can combine edges that never coexisted and do not prove a time-respecting path.

### Cost and unsupported empirical claims

Exact prefix ranks use O(Td) storage per observed triplet and O(Td) insertion time at turn T. Covariance takes O(d^2) memory/update work and Schur/log-determinant operations take O(d^3) time; these are not a demonstrated O(d) estimator. SVD costs O(N^3). Exact bounded simple-path enumeration can be exponential. A hard, explicit enumeration budget reports incomplete attribution rather than silently returning approximate top-K paths. These choices prioritize inspectable calculations for small systems; no sub-1% latency claim is made.

The released materials contain no benchmark traces, embeddings, per-turn expected matrices, or test vectors. Appendix E's scalar spectral score for AUROC/TPR is described verbally without a formula. Its EDR descriptions alternate injection and cascade onset; attribution-lag descriptions also vary. Reproducing Tables 1-14/Figures 2-5 requires clarification and substantial benchmark/model execution beyond this implementation. No accuracy, recall, early-detection, causal-identification, or attack-classification guarantee follows from the tests.

## Continuous measurement (0.2.0)

Algorithm 1 determines the first detection and attribution. The measurement lifecycle now continues until the simulation or replay input ends. Every later observation updates the estimator and spectral signals; it does not restart detection or replace the first alert. `new_alert` identifies the unique confirmation turn, `has_detected` reports the latched state, and `finished` remains false. ACIArena measures final aggregation as well as every debate round. Initialization, minimum samples, detection equations and first-alert timing are unchanged. Older monitor files are not overwritten.
