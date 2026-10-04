# Research and methods

Swarm Lens supports inspecting interactions and designing branch experiments. It does not, by itself, prove that an observed dependency is causal or that an analysis model is accurate.

## Research context

| Component | Role in this project | Evidence boundary |
| --- | --- | --- |
| [MAST](https://github.com/multi-agent-systems-failure-taxonomy/MAST) | Saved-trace LLM judgments using a published failure taxonomy | Judge outputs and cited evidence require review; not human ground truth |
| [ACIArena](https://github.com/Greysahy/aciarena) | Paired control/attack example generation and recorded source interactions | Native task/attack grading is distinct from propagation or cascade labels |
| [CASPIAN](https://github.com/caspian-detector/caspian) | Optional experimental detector implementation | Detection accuracy has not been validated; implementation tests are not benchmark replication |

Swarm Lens's present contribution is infrastructure: an ordered event model, inspectable provenance, branching and intervention workflows, and extensible analysis. These upstream projects supply methods or benchmark context; listing them does not imply their authors endorse this implementation.

## Cascades and failures

A cascade can propagate benign, harmful, or other behavior. An attack is a possible trigger, not a definition of a cascade. Shared answers, common exposure, or agreement do not alone establish propagation. Descriptive tools help locate evidence; reference labels need an explicit operational definition and inspection protocol.

## Suggested experimental record

1. Preserve source data, code revision, exact prompts, outputs, and observed delivery links.
2. State the behavior of interest and labeling criteria before comparing detectors.
3. Separate control and intervention conditions; document shared inputs and model settings.
4. Record the first detection and continue measurement through the end of the run.
5. Retain uncertainty, grader failures, and unscorable cases rather than assigning negative labels.
6. Distinguish repeated-condition estimates from a single illustrative run.

Imported-trace continuation is a new execution in CrewAI, not exact resumption of the original system. Model stochasticity and external side effects limit direct counterfactual interpretations.

## Current limitations

CASPIAN is experimental. MAST can misclassify. Some ACIArena answer parsing is imperfect. The visualization fallbacks are not all recorded delivery edges. Local SQLite data is not automatically reproducible from a Git checkout. See [CASPIAN validation notes](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/observability/caspian/validation.md) and the [MAST guide](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/observability/mast/README.md).

No Swarm Lens paper, DOI, or benchmark accuracy claim is asserted here. Cite the upstream methods separately when using them, and record the exact Swarm Lens commit used in an experiment.
