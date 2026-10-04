# Swarm Lens

Swarm Lens is a workspace for looking at what happens *between* agents. It records the messages, tool calls, and memories in a multi-agent system, lays them out on one timeline, and lets you fork the conversation at any point to try something different. Everything runs locally.

[Watch the demo](https://agencyenterprise.github.io/fractal_swarm_lens/#demo) · [Source code](https://github.com/agencyenterprise/fractal_swarm_lens) · [Report an issue](https://github.com/agencyenterprise/fractal_swarm_lens/issues)

## Where to start

| If you want to… | Read |
| --- | --- |
| Install it and open your first trace | [Getting started](Getting-started) |
| Find your way around a conversation | [Explore traces](Explore-traces) |
| Load the medical debate from the demo | [Examples and datasets](Examples-and-datasets) |
| Record a CrewAI app, or continue a forked run | [Live collection and branching](Live-collection-and-branching) |
| Add your own analysis and view | [Build a plugin](Build-a-plugin) |
| Know what the analyses can and can't tell you | [Research and methods](Research-and-methods) |
| Understand the data model and where files live | [Architecture and storage](Architecture-and-storage) |
| Fix something that isn't working | [Troubleshooting](Troubleshooting) |

These pages describe the `main` branch. One thing that surprises people: runs live in a local SQLite workspace, so they don't travel with the code when you push or pull.

## A note on reading results

It helps to keep three things apart. A **trace** is what happened. An **analysis** is someone's (or some model's) interpretation of it. A **branch** is a new run that happens to share a history with the old one. Most mistakes in this area come from blurring them, for example treating a successful attack as proof that behavior spread, or a judge's label as ground truth. [Research and methods](Research-and-methods) goes into this in more detail.
