# Architecture and storage

## Boundaries

```text
Source application → ordered events → framework → SQLite history
                                         ↓
                                  branches + replay
                                         ↓
                              FastAPI + WebSocket explorer
                                         ↓
                                analysis / UI plugins
```

The core models agents, channels, messages, memory, tools, and environment state. The application layer owns replay, branches, interventions, and extension interfaces. Adapters own SQLite, Git, and artifacts; web code owns presentation. Dataset interpretation stays in source applications.

See the [architecture reference](https://github.com/agencyenterprise/fractal_swarm_lens/blob/main/docs/architecture.md).

## Workspaces

`--data PATH` selects local storage. `data/` and `data/mast-integration/` are separate workspaces even within one checkout. Runs are not added by pulling code. Import saved examples or exchange an exported run bundle to share conversations.

History lives in `history.sqlite`; live jobs and MAST jobs have supporting stores, alongside artifacts and Git checkpoints. Back up the complete workspace when preserving an experiment. A live SQLite WAL may contain committed data: stop processes and use a consistent SQLite backup, rather than copying only an active database file.
