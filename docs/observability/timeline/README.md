# Timeline of relevant events

The `timeline` plugin reads a saved history prefix and returns milestones: the events that changed the course of the run. Each milestone cites event positions, and misaligned behavior is flagged with a severity. Install `.[web,timeline]` and set `OPENAI_API_KEY`.

## Methods

Every method reads the same rendered events, instructions, and calibration examples with the same model, and every prompt starts with a briefing computed from events without a model call: the goals in effect, the agents' roles, and an activity map (messages per sender, tool failures, configuration changes).

- **orchestrated** (default) cuts the history into chunks of `chunk_tokens` tokens (500,000 by default), never splitting an event. Every chunk gets the section prompt with its own briefing (the goals in effect, the agents acting there, its activity) and the instruction to check whether the run's goal is still being met, and the chunks run in parallel. One orchestrator call then reads every chunk's findings and open threads, with the whole-run briefing, merges duplicates, resolves threads that other chunks explain, and returns the timeline. It never reads raw events.
- **single** sends the whole rendered history in one call, with exactly the prompt a section gets (section briefing, "check whether the goal is still met", open threads). It is the baseline: the same reader with no splitting.
- **goal_tree** (top-down) halves the history until each section fits `window_chars`. Each section receives a briefing for its own span (the goal in effect and the agents acting there), checks whether the goal is still met, and reports milestones plus open threads: suspicions that need events outside the section. Each parent combines its two halves, so a thread that spans the boundary can be resolved. Parents at the same depth run in parallel.

Every method returns milestones and open threads in the same schema. A history that fits one chunk or section uses the single call. All methods return every misaligned milestone, uncapped, plus at most `max_milestones` others ranked by severity; the number of flags is the reviewer's workload.

## Guarantees

- Every call is checked against the model's context window before it is sent. An oversized call raises `TraceTooLarge`; nothing is truncated.
- Cited positions the model was not shown are discarded and milestones left without evidence are dropped. The model labels a milestone once, with `kind`; the output's `misaligned` flag is derived from it.
- Responses are checked field by field (types and allowed values); a mismatch is a `ModelError`.
- Each event renders as one line with line breaks escaped, so trace content cannot forge an event marker.
- Provider failures raise `ModelError` (from the shared `swarm_lens.adapters.openai_chat` adapter, also used by MAST) without echoing provider messages. Every error raised by an analysis carries `usage` and `calls`, including the failed request's own billed usage when a response arrived. When one call fails, queued calls are cancelled.
- Each call record keeps its evidence-checked output, so findings can be traced from sections to the root.

## Use

Agent CLI or web interface, up to you: open **Plugins → Misalignment detection → Long-context LLM judge…** in the explorer, or have an agent or script call the HTTP API below.

```sh
curl -X POST localhost:8765/api/branches/BRANCH_ID/analyses \
  -H 'Content-Type: application/json' \
  -d '{"plugin_id": "timeline", "cursor": 199, "config": {"method": "orchestrated", "chunk_tokens": 500000}}'
```

Configuration keys: `method` (`orchestrated`, `single`, `goal_tree`; default `orchestrated`), `chunk_tokens` (500000, orchestrated), `window_chars` (400000 characters, goal_tree sections), `max_milestones` (20 non-misaligned milestones in the final timeline), `leaf_milestones` (8 per chunk or section), `max_workers` (8). Environment: `TIMELINE_MODEL` (default `gpt-5.6-sol`), `TIMELINE_REASONING_EFFORT` (default `medium`), `TIMELINE_CONTEXT_WINDOW` for models without a known window.

## Background jobs

`swarm-lens` registers the `timeline` web extension (`swarm_lens/web/timeline_report.py`) next to MAST. It runs analyses as durable jobs in `<data>/timeline.sqlite`, using the shared `swarm_lens.adapters.jobs.JobStore`:

- `GET /api/plugins/timeline/capabilities`: the manifest also listed in `/api/workspace`, with the model description and a configuration schema with defaults.
- `POST /api/plugins/timeline/preview` with `{"branch_id", "cursor", "config"}`: the number of rendered events, estimated tokens, and planned windows (chunks or sections). It does not call the model.
- `POST /api/plugins/timeline/analyses` with the same body: returns `202` and a queued job. The job runs `Framework.analyze("timeline", ...)`, so a completed job carries the persisted analysis record.
- `GET /api/plugins/timeline/analyses?branch_id=...` and `GET /api/plugins/timeline/analyses/{id}`.

A failed job keeps a plain `error`, its `error_type` and `error_detail`, and the `usage` and `calls` spent before the failure. Jobs pending at startup are marked `interrupted`; they are never repeated automatically.

## Explorer

**Long-context LLM judge…** (section **Misalignment detection**) in the ⋯ menu under Plugins opens a start dialog: choose the method (and the chunk size for orchestrated) and read the free preview of events, estimated tokens, and planned chunks or sections, or why the model is not ready. **Analyze** queues the job and opens it in **Reports**, next to MAST reports. A completed report shows the flags to review, the milestones on a vertical timeline in run order with misalignment flags toned by severity 1–3 (**Flags only** hides the rest), and the open threads. Each cited position is a chip that selects that event in the explorer. Failed and interrupted jobs show their error, its detail, and the usage spent.

## Limitations

Rendering skips recorded prompt captures (`model_input`), which usually repeat context present as messages and memory; content that appears only in a prompt capture is not read. Briefings cap the listed goals, agents, and configuration changes, show agent roles as known at the end of each section, and preview roles up to 600 characters. A failed analysis is not persisted by `Framework.analyze`. Milestones are LLM assessments, not verified labels. The comparison harness is in `experiments/timeline_needles/`.
