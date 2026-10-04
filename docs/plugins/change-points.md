# Change points: where an agent's behavior shifted

`change-points` marks the messages where an agent's behavior shifts abruptly, so a researcher can jump there and
fork. It is a **navigation layer, not an alarm**. Our evaluation shows that a marker means "this agent now writes
differently", not "this agent was manipulated".

![Attack run: both honest debaters get a verbosity marker in round 2, and a band joins them](change-points/aciarena-attack.png)

## What it does

Each agent's messages (`message.created` with a `sender_id`) form one series. For each message the plugin reads
one signal:

| `signal` | Value per message | Metric track |
| --- | --- | --- |
| `verbosity` (default) | log(1 + characters) | `characters` |
| `latency` | log(1 + `metadata.latency_seconds`), else seconds since the agent's previous event | `latency_seconds` |
| `content` | text-embedding-3-small, 16 dimensions (needs `OPENAI_API_KEY` and `swarm-lens[embeddings]`); empty messages are skipped | `content_step` (cosine distance to the agent's previous message) |

An online Bayesian change-point detector ([Adams & MacKay 2007](https://arxiv.org/abs/0710.3742), run-length
recursion, constant hazard) runs on each series. Segments are diagonal Gaussians. The noise variance comes from
successive differences (median, chi-square corrected), and the prior from the agent's history so far. The plugin
also emits `messages_since_change`, the most likely current run length, so the track drops to 1 at each change.

Findings:

- **Marker** (per agent): spans the first message of the new behavior to the message where it was detected.
  `score` is the shift size in noise standard deviations. `data.delay` gives the real detection delay in messages
  and events. `data.run_length_mass` is the model's posterior mass on the current run. It is **not** a calibrated
  probability that the change is real, and the plugin does not call it one.
- **Band** (no agent): once a second agent changes in the same round (`metadata.round`, else the same window of
  `band_window` events), one band spans their changes. Bands are emitted online, so a third agent that changes
  later in that round is not added.
- **Report**: counts per agent and the list of delays.

Params: `signal`, `hazard` (default 1/30), `min_support` (messages the new behavior must hold before it is
reported; default 2), `min_shift` (hide smaller changes, in noise SDs; default 0), `band_window`.

### Online behavior

A boundary is reported when the most likely segment start moves forward and the new segment holds `min_support`
messages. A boundary less than `min_support` messages after the last reported one is a revision of it and is not
reported again. Reported markers are never retracted. The delay is at least `min_support - 1` messages and can be
much longer: on ACIArena it went up to 15 messages.

`on_events` keeps no state on the plugin instance. Each call replays the branch from event 1 up to the end of its
batch, then yields what an online run emits inside the batch. A test checks that the streamed batches equal one
`analyze` run. The cost is quadratic in the number of batches, and the stream must start at event 1. Per-run
streaming state belongs to the live wiring. The only shared state is an embedding memo keyed by text hash, behind
a lock. `analyze(start, end)` also replays from event 1 for context and keeps only findings inside `start..end`.

## Evaluation

Full method, sources and plan: `swarm-lens-research/change-points/README.md` (plan fixed before results, one
amendment from the lead before results). Data: 30 ACIArena medicine tasks, each run with and without a malicious
debater (`debater_0` in every attack run, speaks first), and 10 messageboard runs in which agents may adopt a
questionable workaround. Ground truth was used only for scoring.

### Through the plugin (these defaults; honest seats debater_1 and debater_2; n = 30 paired tasks)

| Signal | Tasks with a change: control / attack | Paired McNemar (both, neither, only attack, only control) | Markers control / attack | Delay median / max (messages) |
| --- | --- | --- | --- | --- |
| verbosity | 2 / 16 | 1, 13, 15, 1, p = 0.0005 | 3 / 32 | 4 / 15 |
| latency | 30 / 29 | 29, 0, 0, 1, p = 1.0 | 76 / 92 | 2 / 12 |
| content | 25 / 30 | 25, 0, 5, 0, p = 0.06 | 55 / 79 | 2 / 15 |

Messageboard (30 agents, 12 used the workaround; "near" = within 2 messages of the first successful workaround
call):

| Signal | Markers | Near adoption | Adopters with a marker near adoption | Markers on non-adopters |
| --- | --- | --- | --- | --- |
| verbosity | 17 | 2 | 2 / 12 | 14 |
| latency | 0 | 0 | 0 / 12 | 0 |
| content | 43 | 10 | 9 / 12 | 26 |

### Reading

- **content ✗.** In the research run with the same detector, every honest agent in every control had a content
  change (60 of 60), and at 10 of the 12 moments when an honest agent switched to the malicious answer, the same
  seat in the matched control run changed at the same round. Content changes follow the debate format (the
  switch from a first answer to "after reviewing the other solutions"), not persuasion.
- **verbosity ✓ for attack presence only.** Honest agents' messages get longer in attack runs (15 tasks only in
  attack vs 1 only in control). The changes are not at capitulation: of the 6 tasks where honest agents took the
  malicious answer, only 2 have a verbosity marker, and most markers are in tasks where the honest agents kept
  their answer (as in the screenshot above, medicine-014). Same in the control of that task:

  ![Control run of the same task: no markers](change-points/aciarena-control.png)
- **latency ✗.** Markers appear in nearly every run, attack or control.
- **messageboard ✗.** Content markers are near adoption for 9 of 12 adopters, but 26 of 43 markers are on agents
  that never used the workaround. In the research run no signal beat a random-placement baseline. In run-kimi-01
  below, the content marker is on reviewer_2, who did not use the workaround, and the agent that did has none.

  ![Messageboard run-kimi-01: content markers on reviewer_2](change-points/messageboard-kimi-01.png)

One model (gpt-4o-mini, temperature 0) on ACIArena, two (kimi, qwen) on the messageboard. The plugin's defaults
differ a little from the research run: `min_support` also requires the earlier segment to hold 2 messages,
which removes round-1 changes (research: verbosity 15% vs 70% of honest agents; plugin: 2 vs 16 tasks), and the
content signal uses the API's 16-dimension embeddings instead of a random projection.

### What it is and what it is not

It is a way to cut a 200-event run down to the few messages per agent where the style or content shifted, with
one click to the event and the fork actions. It is not a detector of manipulation, capitulation or misalignment,
and no score it shows is a probability.

## Limitations (open review points)

An adversarial review (gpt-6-astra) of the research run raised these objections. Quoted verbatim; we fixed 3, 4
and 10 in the plugin and corrected the wording that 2, 9 and 11 showed was wrong. The rest stay open:

> **1. The primary evaluation does not test the lead's primary question.** "Neither direction of semantic change
> nor movement toward the target enters P1 or P1b." "'The pass rule is met' is evidence that the rule was
> inadequate, not that the intended task succeeded."

> **2. Messageboard precision excludes all non-adopters, materially inflating the reported numbers.**
> "content/BOCPD | 6/11 = 54.5% | 6/53 = 11.3%"

> **3. "One-message delay" is false, and the evaluation hides the actual online delay.** "The condition
> guarantees a delay of at least one message, not exactly one." *(Fixed: every marker reports `data.delay`.)*

> **4. The alarm list can count revisions of a boundary as additional change points.** "'More CPs' may partly
> mean greater MAP-boundary instability under attack, not more actual regime changes." *(Fixed: revisions closer
> than `min_support` messages are not reported; a test guards it.)*

> **5. The random baseline has a concrete support mismatch.** "BOCPD can and frequently does report index 1, but
> the baseline never samples it."

> **6. The exploratory adoption McNemar analysis treats clustered observations as independent.** "There are 12
> adoption events but only six tasks."

> **7. Messageboard exposure/adoption labels are inferred from arbitrary previews, not validated action
> records.**

> **8. The promised auditable ACIArena output is absent.** "per_task contains letters only."

> **9. "BOCPD is as good as or better than PELT" does not follow from larger count differences.**

> **10. The exported "probability" is not a calibrated probability that the annotation is a real change.**
> *(Fixed: the score is the shift size; the posterior mass is named `run_length_mass` with a note.)*

> **11. The positive navigation conclusion is selected qualitative evidence.** "This is a hypothesis for the next
> phase, not a demonstrated positive result."
