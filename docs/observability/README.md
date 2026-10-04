# Observability methods

Observability is an optional framework extension. The core reducer, history stores, and source contracts do not depend on a particular method or numerical library.

[MAST](mast/README.md) is the on-demand LLM analysis plugin for saved traces. It uses its own job flow and a composed `/api/plugins/mast` router, discovered by the web UI at startup; its jobs live in the shared plugin job store and its evidence becomes [annotations](../plugins.md) on the timeline. It does not implement the streaming `ObservabilityMethod` below. CASPIAN remains an experimental library method with no dedicated web API.

Each method lives under `src/swarm_lens/observability/<method>/`, with its own `tests/observability/<method>/`, `docs/observability/<method>/`, and `examples/observability/<method>/`. [CASPIAN](caspian/README.md) is the first implementation. Importing `swarm_lens.observability` does not import NumPy.

An `ObservabilityMethod` exposes `id`, `version`, `finished`, `update(observation)`, and `describe()`. Input types and results belong to the method. A versioned, application-owned `HistoryAdapter` implements `observations(history)` and `describe()`. It defines turns, encoders, source/target relationships, and source-specific interpretation. It receives only the branch history prefix selected by the analysis cursor.

`ObservabilityPlugin(method_id, version, factory, adapter)` creates a fresh method for every analysis, processes observations sequentially until completion, and includes the resolved method configuration and adapter provenance in its result. It is an [Analyzer plugin](../plugins.md): register it with `PluginService(framework, (plugin,))` (or a plugin factory) and `PluginService.analyze` stores the plugin version, branch and range, params, input digest, and the method output as `output.report`. Pass a pydantic model as `params` to make the method configuration a params form. No method state is shared between branches or analysis invocations.

A method result is analysis data, not an automatic intervention. Applications can compose a second plugin or explicit user action to fork/intervene. Recorded prompt edits do not imply that a runtime executed them. See the [branch example](../../examples/observability/caspian/branch_plugin.py).

To add a method, implement that protocol, supply an application adapter and factory, and provide method-specific tests, documentation, and examples. Add its dependencies as an optional extra. Keep source schema mapping outside this package. Vendored upstream implementations must use a Git submodule; CASPIAN is independently implemented from the paper and imports no upstream source.
