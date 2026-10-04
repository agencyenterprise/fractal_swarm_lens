# Swarm Lens

**Observe an agent system. Preserve its history. Branch an experiment.**

Swarm Lens is a local, extensible workspace for multi-agent observability research. It connects recorded messages, tools, memories, and interventions to an explorer, branching model, and analysis plugins.

[Watch the demo](https://agencyenterprise.github.io/fractal_swarm_lens/#demo) · [Source code](https://github.com/agencyenterprise/fractal_swarm_lens) · [Report an issue](https://github.com/agencyenterprise/fractal_swarm_lens/issues)

## Choose a starting point

| I want to… | Start here |
| --- | --- |
| Run the explorer and import my first trace | [Getting started](Getting-started) |
| Inspect a conversation and compare outcomes | [Explore traces](Explore-traces) |
| Load the medical debate shown in the demo | [Examples and datasets](Examples-and-datasets) |
| Observe a CrewAI app or continue a branch | [Live collection and branching](Live-collection-and-branching) |
| Add an analysis API and browser view | [Build a plugin](Build-a-plugin) |
| Understand methods and research limitations | [Research and methods](Research-and-methods) |
| Understand the data model and SQLite migration | [Architecture and storage](Architecture-and-storage) |
| Resolve setup and missing-data problems | [Troubleshooting](Troubleshooting) |

## Current implementation

These guides describe the `main` implementation, including the ACIArena scenarios, storage v2, and modular web plugins. The demonstrated feature set is available on `main`. Examples stored in a local SQLite workspace are not automatically shared through Git.

## Research stance

A trace records evidence. An analysis interprets it. An intervention creates another execution. Keep those three distinct when drawing conclusions. Cascade effects need not be harmful, and attack success does not by itself establish a cascade.

Swarm Lens is evolving research infrastructure. CASPIAN is an experimental feature and, to our knowledge, the first public implementation of the paper by [Venkatesh et al. (2026), arXiv:2605.19240](https://arxiv.org/abs/2605.19240). MAST provides saved-trace analysis. See [Research and methods](Research-and-methods).
