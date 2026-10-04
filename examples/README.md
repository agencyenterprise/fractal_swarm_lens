# Examples

**CrewAI is the primary runtime.** Start with [its setup and integration guide](crewai/README.md), or follow the [repository quickstart](../README.md#quickstart).

Run commands from the repository root with `.venv-crewai` activated. Use one server and one `--data` directory for the examples you want to compare. The current documentation uses port **8767**; pass it explicitly to collectors because their default remains 8766.

## Choose the smallest useful run

| Workflow | Local inputs | Provider calls | Instructions |
| --- | --- | --- | --- |
| Inspect a long conversation | Bundled AI Village research recording, about 4.5 MB compressed | None | [Import / prepare](crewai/README.md#ai-village-perform-novel-research) |
| Check capture and tools | Three-agent arithmetic crew with deterministic responses | None | `python -m examples.crewai.demo --offline --url http://127.0.0.1:8767` |
| Observe an LLM application | Same arithmetic crew with GPT-5.5 | Yes | Omit `--offline` after configuring `OPENAI_API_KEY` |
| Compare control and attack | Bundled ACIArena pairs, including medicine | None during import | [ACIArena examples](aciarena/README.md#complete-examples-for-the-explorer) |
| Inspect medical simulator data | An existing messageboard batch with `manifest.json` and event logs | None during import | [Messageboard importer](messageboard/README.md) |
| Analyze failure modes | Any selected saved branch/cursor | Yes, only when requested | [MAST guide](../docs/observability/mast/README.md) |

Importing records and creating a branch do not execute agents. The timeline's play button replays history. **Create branch and run live** generates new output using the runtime shown in the preview.

## Work efficiently

1. Install the [CrewAI dependency lock](crewai/requirements.lock) once. Reuse the environment for saved imports, the explorer, and MAST.
2. Import bundled recordings before generating new benchmark data. Upstream submodules and benchmark-only packages are unnecessary for those imports.
3. Run the offline crew first to verify collection. For a real run, the arithmetic example is much smaller than a 15-agent AI Village continuation or a 20-round debate pair.
4. Fork near the point you want to study. The selected prefix is preserved; you do not need to regenerate its earlier messages.
5. Use MAST's preview before submitting. Chunking preserves long traces but adds per-chunk assessment/evidence calls and reconciliation calls. Old reports can be reopened without another request.
6. Use a new `--data` path for a clean experiment. Keep your primary workspace and its artifacts intact.

## What remains in this directory

- `crewai/`: the primary native integration example and the selective AI Village research recording.
- `aciarena/`: saved benchmark imports, upstream generation, and existing research protocols. Its fixtures also exercise CrewAI continuation and MAST input integrity.
- `messageboard/`: an importer for the medical-data simulator's saved logs; it does not generate runs.

The old gated AI Village leader-election loader and standalone CASPIAN demo CLIs were removed when onboarding moved to CrewAI. Numeric helpers still required to test CASPIAN moved to `tests/support/`; the optional method and its tests remain. Existing `data/` directories, artifacts, upstream repositories, and separate research experiments were not removed.

Example recordings have their own provenance and upstream terms. See [third-party notices](../THIRD_PARTY.md).
