# Research and methods

Swarm Lens helps you look closely at how agents interact and set up experiments by branching. It can't, on its own, tell you that one agent caused another's behavior, or that an analysis model got it right. Those claims still need an experimental design behind them.

## Methods we build on

| Component | What it does here | What to keep in mind |
| --- | --- | --- |
| [MAST](https://github.com/multi-agent-systems-failure-taxonomy/MAST) | An LLM judge labels saved traces with the failure taxonomy from [Cemri et al. (2025), arXiv:2503.13657](https://arxiv.org/abs/2503.13657) | Read the judge's output and the evidence it cites; it isn't a human annotation |
| [ACIArena](https://github.com/Greysahy/aciarena) | Paired control/attack runs from [An et al. (2026), arXiv:2604.07775](https://arxiv.org/abs/2604.07775) | Its grader scores the task and the attack, not whether behavior propagated |
| [CASPIAN](https://github.com/caspian-detector/caspian) | Experimental cascade detection and attribution | Uses cross-channel causal monitoring, from [Venkatesh et al. (2026), arXiv:2605.19240](https://arxiv.org/abs/2605.19240) |

What Swarm Lens itself adds is the infrastructure around these: an ordered event model, provenance you can inspect, branching and intervention, and a way to plug in more analyses. The methods and benchmarks come from the projects above. Their authors haven't reviewed or endorsed this implementation.

## About CASPIAN

CASPIAN is included as an experimental feature. As far as we know, this is the first public implementation of the method in [Venkatesh et al. (2026)](https://arxiv.org/abs/2605.19240). Expect rough edges, and please [tell us](https://github.com/agencyenterprise/fractal_swarm_lens/issues) when you find them.

## Cascades vs. failures

A cascade is behavior spreading from agent to agent. What spreads might be harmful, harmless, or just odd. An attack is one way to start a cascade, but it isn't the definition of one. And when agents give the same answer, or saw the same input, or agree with each other, that by itself doesn't show anything propagated. The descriptive views help you find evidence. To label cascades reliably, you need a written definition and a consistent way of checking traces against it.

## Keeping a good experimental record

1. Save the source data, code revision, exact prompts and outputs, and the observed delivery links between agents.
2. Write down what behavior you're looking for, and how you'll label it, before you compare detectors.
3. Keep control and intervention conditions separate, and note which inputs and model settings they share.
4. Record when something is first detected, and keep measuring until the run ends.
5. Keep uncertain, failed, and unscorable cases as they are. Don't fold them into the negatives.
6. Be clear about whether a number comes from repeated runs or from a single illustrative one.

Continuing an imported trace starts a new CrewAI run. It doesn't resume the original system exactly. Between model randomness and tools that touch the outside world, a branch isn't a clean counterfactual, so treat comparisons with that in mind.

## What we'd like to do next

- **Reproducible benchmark workflows.** Package trace exports, model settings, prompts, and code revisions so others can rerun and compare experiments.
- **Check the analyses against human labels.** Compare MAST reports and CASPIAN output with independently reviewed traces, broken down by scenario and behavior.
- **Follow propagation over time.** Use paired control and intervention runs to see how behavior moves through messages, tools, and memory.
- **More frameworks.** Capture more kinds of interaction, and support branching in runtimes beyond CrewAI.
- **Easier sharing.** Export reports and the trace evidence together, with citations for the methods used.
