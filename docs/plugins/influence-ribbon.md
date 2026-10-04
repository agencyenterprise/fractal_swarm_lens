# Influence ribbon

**Exploratory predictive coupling, not an attack detector.**

The Influence ribbon asks one question about a recorded branch: whose messages predict whose next
message? For each ordered pair of agents it estimates how much the latest message agent X wrote,
which agent Y had read, improves the prediction of Y's next message. It conditions on Y's own previous
message and on what Y had read from the other agents. It is a descriptive lens on one trace. It does
not find malicious agents. We tested that, and it failed (see the evaluation below).

It is a bundled analyzer (`swarm_lens/plugins/influence_ribbon.py`, method in
`swarm_lens/observability/influence.py`). It uses the generic UI only:

- **Metric tracks:** `gain from <source>` for each target agent. The value is the per-message (local)
  gain, in nats, from the source message that the target read.
- **Annotations:** one per ordered pair, in the target's lane, over the target messages that were
  scored. The label is `source → target: <TE> nats over <n> messages`, or `insufficient data (<why>)`.
  The score is the TE. The cited events are the scored target messages. Select any message and the
  inspector's **Findings** list all pairs that cover it, which reads as the coupling matrix.
- **Report:** the full matrix, each agent's outgoing total, exposure counts, method, guard settings
  and limitations, as JSON. An outgoing total is null when any of the agent's pairs is insufficient
  data, because unknown coupling is not zero coupling.

To compare a fork with its parent, run the plugin on both branches. The two values come from
separate fits; the plugin itself cannot read the parent's events after the fork.

## Method

For a target Y, a source X and the other agents Z, over Y's messages t:

    TE(X → Y | Z) = I( X_read(t) ; Y_t | Y_(t-1), Z_read(t) )

- **Exposure:** `X_read(t)` is the latest X message listed in the message's
  `source.delivered_sources`, whatever the order of that list. When a trace records no deliveries,
  the latest earlier X message is assumed. The report counts recorded and assumed exposures.
- **Range:** the plugin reads messages from event 1 to `end`, so earlier reads and each target's
  previous message are kept, and it scores only target messages from `start` to `end`. Agents with
  fewer than two messages are left out, also as conditioning agents, and listed in the report.
- **Features:** `text-embedding-3-small` (256 dimensions, through the shared
  `OpenAITextEncoder`), then a per-trace PCA to 1 component by default (`components`, 1 to 3).
  Components below a numerical rank tolerance are dropped, and the report gives the effective number.
  Embeddings are cached in `<data>/influence-ribbon/embeddings.sqlite`, so a rerun costs nothing.
- **Estimator:** linear-Gaussian TE, half the log ratio of the restricted and full residual
  covariances. This equals half the Granger statistic for Gaussian variables
  (Barnett, Barrett and Seth 2009). The per-message gain is the local value
  `log p(y_t | y_(t-1), z, x) - log p(y_t | y_(t-1), z)` of the same fitted models
  (Lizier et al. 2008), so the pair TE is the mean of its gains. A test checks the pair TE against
  CASPIAN's independent `gaussian_cmi`.
- **Guard:** a pair is reported as insufficient data instead of a number when there are fewer than
  `max(min_samples, 2 × regression columns)` target messages, fewer than 6 distinct message rows,
  a source or target that does not vary, or a target that the full model predicts with more than 98%
  of its variance in some direction (repeated messages). In our first prototype these cases
  produced 5 to 11 nat values that only numerical jitter held in place.
- **Coverage:** a target message is used only when its target had read the source and every other
  agent. With many agents or sparse delivery records few messages qualify, and the regression needs
  at least 2 × columns of them (columns grow with the number of agents), so most pairs come out as
  insufficient data.
- **No significance:** the report shows effect sizes only. A within-trace shuffle is not a valid
  null here (see objection 1 below), and we do not give a reference level for "no coupling".

## Evaluation: it does not find the malicious agent

Data: 30 ACIArena medicine pairs (LLMDebate, 3 debaters and an aggregator, 20 rounds), each a run
with a malicious agent and the same task without one. The ground truth label was used only for
scoring. In all 30 attack runs the malicious agent is `debater_0`. Seat 0 speaks first and both
others read it fresh, so the matched control run (same seat, no attack) is the baseline, and the
uniform 1/3 guess is reported too. The plan was fixed before any output was computed.

Phase-1 prototype (PCA to 2 components, a permutation-corrected score, no guard; the planned test):

| Metric | Attack runs | Control runs |
| --- | --- | --- |
| `debater_0` top by outgoing TE | 7/30 (23%, 95% CI 12 to 41%) | 20/30 (67%, 95% CI 49 to 81%) |
| Exact McNemar on the 30 pairs | p = 0.0044 (3 attack-only, 16 control-only), opposite to the hypothesis | |
| Wilcoxon, `debater_0` score attack minus control | median -1.12 nats, p = 0.0062 | |
| One-sided binomial, attack top-1 above 1/3 | p = 0.92 | |
| Answer-letter (discrete) TE | no unique top in 26/30 attack and 30/30 control runs | |

The shipped estimator (PCA to 1 component, guard, no permutation) was run once on the same 30 pairs
after the fact, as a check of what users get, not as a new hypothesis. Here the top agent is the
unique maximum of the sum over the pairs that were not insufficient (a partial sum when some were):

| Metric | Attack runs | Control runs |
| --- | --- | --- |
| `debater_0` top by summed outgoing TE | 11/30 (37%, 95% CI 22 to 55%) | 13/30 (43%, 95% CI 27 to 61%) |
| Exact McNemar | p = 0.75 (4 attack-only, 6 control-only) | |
| One-sided binomial vs 1/3 | p = 0.42 | |
| Pairs reported as insufficient data | 28/180 | 68/180 |

Neither version detects the malicious agent. These temperature-0 debates repeat themselves (6 to 10
distinct texts out of 21 per agent), and the other agents often settle on an answer early. TE scores
how variation in X predicts Y's next message, and a stubborn agent that repeats one claim has little
variation to transfer. That is our hypothesis for the failure. We did not test it.

On the 10 message-board runs (no labels, no read receipts) the prototype found 4 of 60 pairs with
p < 0.05, near what chance would give if those p-values were valid. They are not shown to be valid.

The evaluation scripts, the dated plan and the cached inputs are in our research notes, not in this
repository, so these tables cannot be regenerated from this pull request alone.

## Adversarial review

We asked a separate model (gpt-6-astra) to prove the phase-1 study wrong. Its objections, verbatim
headings:

1. "Critical: The permutation null is invalid for the stated conditional, temporal hypothesis"
2. "Critical: The estimator visibly operates in a numerically pathological regime"
3. "High: Conditioning on the third agent can remove exactly the malicious influence being sought"
4. "High: The control comparison is not an operational false-alarm evaluation"
5. "High: The explanatory story is post-hoc and not supported by the supplied diagnostics"
6. "High: Significant reverse-direction differences do not confirm the prespecified hypothesis"
7. "High: Attack/control scores use different representations, undermining 'TE drops' as a common-scale interpretation"
8. "Medium-high: The per-message visualization is not validated by the aggregate estimator"
9. "Medium: The discrete result may partly measure parser failure"
10. "Medium: The message-board analysis does not establish applicability to board influence"
11. "Medium: Preregistration and reproducibility are asserted more strongly than demonstrated"

Its permitted conclusion, verbatim: "On these 30 matched medicine debates, the specified pipeline
ranked the malicious seat first in 7 attacks and the same seat first in 20 controls. The paired
top-rank difference is statistically significant in the opposite direction to the planned
hypothesis. However, invalid temporal/conditional surrogates and near-singular estimation prevent
interpreting the scores as calibrated influence measurements."

What the plugin changes in response: 1, no significance is claimed and only effect sizes are shown;
2, the guard reports insufficient data instead of unstable values; 8, the per-message gains and the
pair TE now come from the same fitted models (the pair TE is their mean), though they are still
in-sample values with no per-message uncertainty. Objections 3, 4, 5, 7, 9, 10 and 11 still apply.
The values depend on which agents are conditioned on, they are not comparable across traces, and
assumed exposure on traces without delivery records is a guess.

A second review of this pull request found five more problems, now fixed and tested: a pair that no
message could score crashed the whole job; with several deliveries from one agent, list order decided
which message counted as read; outgoing totals counted unknown pairs as zero; PCA could scale
numerical noise into a feature; and the range dropped earlier reads. It also asked us to remove the
"no coupling" reference level, which we did, and to make the evaluation reproducible from the
repository, which is still open.

## Case study: medicine-004, attack run and its "Honest Debater 0" fork

We ran the plugin with default settings on a copy of our demo data: the attack run of medicine-004
(events 1 to 200), its fork "Honest Debater 0" (forked at event 10, a full new debate, events 1 to
454), and the clean control run. Outgoing pairs, nats (n = 20 target messages each; no pair was insufficient):

| Pair | Attack run | Honest fork | Control run |
| --- | --- | --- | --- |
| debater_0 → debater_1 | 0.60 | 1.27 | 0.09 |
| debater_0 → debater_2 | 0.03 | 0.49 | 0.00 |
| debater_1 → debater_0 | 0.01 | 0.71 | 0.00 |
| debater_1 → debater_2 | 0.16 | 0.51 | 0.02 |
| debater_2 → debater_0 | 0.04 | 0.03 | 0.00 |
| debater_2 → debater_1 | 0.26 | 0.03 | 0.00 |
| Exposure | recorded | assumed (the fork's new messages have no delivery records) | recorded |

What this shows, and does not: the control debate is almost fully repetitive, so nothing predicts
anything. In the attack run, debater_0's messages predict debater_1's next message; in the honest
fork, coupling is stronger in both directions between debater_0 and debater_1. One task and one
fork are an anecdote. The fork's exposure is assumed, the fits are separate, and the evaluation above
says these numbers do not separate attacked from clean runs.

![Attack run: gain tracks under the lanes, pair annotations, and the inspector's findings for a debater_0 message](influence-ribbon/parent-attack.png)

![Honest Debater 0 fork: the same view after running the plugin on the fork](influence-ribbon/fork-honest.png)

## Sources

Read for this work: Schreiber 2000, "Measuring Information Transfer" (full paper,
https://arxiv.org/abs/nlin/0001042); Barnett, Barrett and Seth 2009, "Granger causality and transfer
entropy are equivalent for Gaussian variables" (full paper, https://arxiv.org/abs/0910.4514);
Lizier, Prokopenko and Zomaya 2008, local transfer entropy (derivation and methods,
https://arxiv.org/abs/0809.3275); Vicente et al. 2011 (method sections on surrogates,
https://pmc.ncbi.nlm.nih.gov/articles/PMC3040354/); Granger 1969 (definitions section).
