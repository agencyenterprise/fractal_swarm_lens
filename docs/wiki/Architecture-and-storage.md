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

## Storage v2

The storage implementation introduced in PR #6 stores long content through a compressed text table and snapshots as bounded delta chains. Public framework reads reconstruct original content. Raw SQL readers must account for content digests.

Opening an older workspace with this code migrates its schema to v2. **Stop every server, collector, and script using it, then back up the workspace before upgrading.** Older code cannot safely use the migrated database; restore the backup to return to it.

Migration retains existing inline text and snapshots. It does not retroactively compact all old rows. Entirely new versions of growing conversation memories can still cause quadratic growth; whole-text deduplication is not chunk-level deduplication. State reads may be slower even when writes and storage improve.
