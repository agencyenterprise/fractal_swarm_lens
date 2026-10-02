# Architecture and invariants

## One event envelope

`Fact(kind, data, occurred_at, source, id)` is the application input. The framework turns it into `Event(id, branch_id, position, kind, data, occurred_at, recorded_at, source, schema_version)` after applying the domain rules. Positions define replay order; timestamps place events on the visual timeline. They are separate because source records can have equal or imperfect clocks.

Agents, channels, messages, memory slots, tool calls, and environment have stable IDs. References use IDs, not nested copies. The event envelope carries timing and provenance. Each entity has a small set of common fields plus application metadata. Provider responses and large payloads can be stored by content digest and referenced in metadata. `observation.recorded` retains application observations that do not mutate a standard entity.

The reducer validates references and lifecycle transitions. For example, a message requires an existing channel and an active sender, and a completed tool call requires a running call. An agent removal preserves identity and prior history while changing its active state and channel membership.

## Branch semantics

A branch belongs to one run and records `parent_id`, `fork_position`, and `head`. Its effective history is its parent's prefix through the fork position followed by its own events. Parent events after the fork never become visible to the child. A child can itself be forked at any available cursor, including an inherited point before its own creation.

Replay loads the closest eligible snapshot, then applies events through the requested cursor. Reverse scrubbing reconstructs the earlier state; it does not attempt to undo mutations. Snapshots accelerate reads and can be regenerated from history. Reads return detached state, so a plugin cannot alter persisted history by editing an object it received.

Writes use an expected head. SQLite commits each batch and its relational projections atomically; an intervening writer produces a conflict. An ingest spanning multiple batches retains earlier successful batches if a later batch fails. Applications own retry/resume policy and stable source IDs. Runtime continuations also reject output if the branch changed while that output was being produced.

An intervention is another event with its actor and origin recorded. The UI forks recorded or historical state before editing it. The library leaves branch policy to its embedding application and only permits appending at the current head.

## SQLite and Git

SQLite is the query and replay store. It contains runs, branches, events, snapshots, analysis records, entity identities, and revision tables for agents, channels, membership, messages, memory, tools, and environment. Revision queries must follow branch ancestry and cursor limits; scanning a projection table alone combines multiple histories.

Git is the checkpoint/version adapter. A bare repository stores manifests, canonical state, and the branch's local events. A child checkpoint has the exact historical fork checkpoint as its ancestor. Creating an older checkpoint does not rewind a branch's latest ref. There is no shared mutable checkout. Git is not in the ingestion hot path, and there is no automatic merge of divergent agent runs.

SQLite commits and Git checkpoints are separate operations. A checkpoint failure does not roll back a valid SQLite event. Checkpointing is repeatable, and artifacts are stored separately by hash. Back up the SQLite database using its backup API and preserve `history.git` and `artifacts/` together when moving a workspace.

## Extension ownership

The application composes implementations of the ports. A source understands dataset-specific keys, joins, task boundaries, and ordering. A runtime knows how to provision an execution environment, restore supported state, and call models/tools. A plugin can read the selected state and event history, enumerate branches, fork, and submit interventions. Plugin constructors can receive application services such as an artifact store, model client, probe, or steering backend.

Plugin results persist the plugin version, configuration, selected branch/cursor, input history digest, and output. The provided Activity plugin counts recorded activity; it does not infer mental states or alignment outcomes.

For model-internal experiments, store the proposed intervention and its configuration in branch metadata/state, then have the runtime apply it and emit observations recording the actual result. A recorded state edit alone is not evidence that a provider or model applied the change.

## Scaling without changing the core

The current explorer loads one selected branch's event summaries and compact state. Timeline rendering limits event DOM nodes to the horizontal viewport. For larger runs, move timeline paging and aggregate state queries into a query adapter, reduce snapshot duplication, and queue runtime work. Replace SQLite with another `HistoryStore` if concurrent write load requires it. UI layouts, live subscriptions, and custom inspectors remain outer concerns.

## Optional observability

`observability/` supplies method protocols and a versioned plugin bridge; method-specific numerical code is isolated in subpackages such as `observability/caspian/`. It depends on the domain event type for its adapter contract, while the core/application do not import it. Application adapters define feature encoders, observed source/target pairs, and turn boundaries. Each plugin analysis starts a fresh monitor over the selected effective history prefix, preserving branch isolation. See [observability](observability/README.md) and [CASPIAN's specification limits](observability/caspian/coverage.md).
