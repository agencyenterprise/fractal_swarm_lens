# Architecture and storage

## How the pieces fit

```text
Source application → ordered events → framework → SQLite history
                                         ↓
                                  branches + replay
                                         ↓
                              FastAPI + WebSocket explorer
                                         ↓
                                analysis / UI plugins
```

The core models agents, channels, messages, memory, tools, and environment state. On top of that, the application layer handles replay, branches, interventions, and the extension interfaces. Adapters deal with SQLite, Git, and artifacts, and the web code only handles presentation. Anything specific to a dataset, like how to interpret its fields, stays in the source application.

The [architecture reference](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/architecture.md) has the details.

## Workspaces

`--data PATH` picks which local workspace to use. `data/` and `data/mast-integration/` are two separate workspaces, even inside the same checkout. Pulling new code never adds runs; to share conversations, import the bundled examples or pass around exported run bundles.

History is stored in `history.sqlite`. Live jobs and MAST jobs have their own stores next to it, along with artifacts and Git checkpoints. If you want to preserve an experiment, back up the whole workspace directory. While a server is running, the SQLite write-ahead log can hold data that's committed but not yet in the main file, so stop everything first and take a proper SQLite backup rather than copying the database file on its own.
