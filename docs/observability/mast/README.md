# MAST saved-trace analysis

Collect or import a conversation, select a saved branch/cursor, and explicitly request MAST analysis. There is no live-follow judge, automatic model call on ingestion, or CASPIAN-specific API.

The plugin uses [MAST's published notebook](https://github.com/multi-agent-systems-failure-taxonomy/MAST/blob/a70542e541b2104ef8fcd785778179e173fb8d70/llm_judge_pipeline.ipynb), definitions, and examples at commit `a70542e541b2104ef8fcd785778179e173fb8d70`. It returns a summary, a task-completion judgment, and judgments for 14 failure modes. These are LLM assessments, not human-verified labels or cascade detections.

## Run

```sh
python -m pip install -e '.[web,mast]'
swarm-lens --data data --env-file .env --port 8765
```

Set `OPENAI_API_KEY` on the server or in the selected `.env`. The default judge is now `MAST_MODEL=gpt-5.5`, as requested for this application. The upstream notebook used `o1`; this model choice is an explicit evaluation variant, recorded with the requested and resolved model names in each result. GPT-5.5 uses medium reasoning and omits the temperature parameter. Its context budget is 1,050,000 tokens, with 16,384 reserved for completion and a safety margin of 5% of the context window (at least 4,096 tokens) for tokenizer/framing differences and provider admission limits. The separate `MAST_MAX_INPUT_TOKENS` cap defaults to 240,000 tokens per complete prompt, including taxonomy/examples; this avoids relying on near-million-token requests being admitted by the provider. Raising it is an explicit deployment choice. Set `MAST_CONTEXT_WINDOW` when configuring another model whose context limit is not registered. Credentials never reach the browser. Saved traces and results remain available without credentials; starting an analysis requires them.

Open http://127.0.0.1:8765. Use the core **Import trace** action (`POST /api/traces`) to paste or upload a UTF-8 transcript, or select an existing Swarm Lens run. Raw imports preserve the transcript as `observation.recorded`, without inventing agent identities or timestamps. JSON/JSONL files are retained as text, not guessed into a schema. Upload the conversation itself without reference annotations that would reveal the desired judgment.

Select **MAST analysis**, indicate whether the trace is complete, then **Analyze saved trace**. This setup dialog opens the submitted job in the **MAST reports** workspace view. The report displays its progress, summary, all 14 answers, raw response, provenance, and a JSON download. Use **Timeline** to return to the same cursor, filters, and zoom, or **View analyzed snapshot** to move explicitly to the report's input boundary.

Before submission, the dialog checks the entire saved snapshot locally and shows event/message/memory counts and estimated prompt tokens, including MAST's definitions and examples. This preview makes no model request and creates no job. If the trace exceeds the per-request character or token budget, it previews the chunk count and the extra model requests instead of asking you to shorten the trace. The complete ACIArena 20-round examples fit after exact repeated text is stored once; selecting fewer rounds is unnecessary.

Saved reports can be reopened from **MAST reports** without opening the setup dialog. Each has a **Report link** and **Copy report link**, using `/#branch=…&cursor=…&view=mast&report=…`. Reloading or opening that URL restores its conversation and saved report; it never starts another analysis. Links work for people with access to the same Swarm Lens server (a localhost URL remains local). A running report refreshes while visible; leaving it stops browser polling but does not cancel server work. Importing, refreshing, and receiving new events do not trigger a judgment.

The explorer's `WorkspaceViews` registry lets UI plugins register a tab, panel, and show/hide lifecycle. MAST uses this slot alongside the retained timeline, conversation feed, and inspector; future visualizations can use the same interface. Switching conversation branches returns to the timeline to avoid displaying a report for the wrong branch.

### Automatic chunking and reconciliation

Plugin **0.5.0** keeps the existing whole-trace path for inputs that fit. Larger inputs use a chunked extension of MAST:

1. Freeze the selected branch/cursor and split the lossless projection in chronological event order. Each chunk includes up to two preceding events when they fit. All chunk inputs include their source boundaries and are explicitly marked partial, even if the full trace is complete. Local boundaries do not imply task termination.
2. If one event alone exceeds the budget, divide its serialized data into exact Unicode slices, preserving its event ID, position, kind, timestamp and character offsets. No tail is discarded. Repeated-text references are rebuilt independently in each chunk. The original full trace remains available for evidence lookup.
3. Run independent chunks concurrently with a bounded thread worker pool. Within each chunk, the published MAST judgment runs before its evidence-localization request. Both full prompts are checked against the model's input budget. A single coordinator saves completed stages as they arrive; the UI shows active chunk ranges and completed counts. Stage lists and reconciliation inputs retain chronological order regardless of completion order.
4. Ask the judge to reconcile the chronological summaries, judgments and localized evidence. This is **not a union or majority vote**: the reconciliation prompt asks for contradictions, later clarification, recovery and cross-chunk dependencies. It produces the final 14 judgments, task-completion assessment and evidence spans. Duplicate occurrences are collapsed, and references are validated against IDs supplied to reconciliation and the frozen source. Unsupported, missing or unfinished results remain unknown and require review.
5. If all chunk reports cannot fit a reconciliation request, reduce consecutive groups and reconcile those results in further bounded passes. Every intermediate result is retained. If even two reports cannot fit, the job fails with its completed stages preserved; nothing is silently truncated.

The report shows **saved analysis stages**, individual chunk summaries, downloadable exact inputs/results, reconciliation progress and one combined assessment. Failed or interrupted jobs retain completed stages and never publish a partial combined result as complete. Restarting the server does not repeat paid calls automatically. Creating a new analysis starts a new job; automatic resume is not implemented. Provider failures retain safe structured diagnostics (HTTP status, recognized error code/parameter, request ID and known numeric size limits) in the report and JSON; arbitrary provider messages and request bodies are not exposed.

Set `MAST_WORKERS` to 1–8 (default **3**). It limits concurrent chunks per job and concurrent provider calls across all jobs sharing the server's MAST plugin, including single-trace and reconciliation calls. Set it to 1 for sequential execution or reduce it when provider rate limits are tight. Reconciliation begins only after every chunk succeeds; hierarchical reconciliation remains sequential. If a chunk fails, no new chunks are scheduled, queued work is cancelled where possible, and successful in-flight results are saved before the job reports failure. This does not retry failed requests or change the prompts, token budgets, request count or classifications. Parallel calls can hit provider rate limits sooner, so speedup is not guaranteed.

The default `MAST_MAX_TRACE_CHARACTERS=4000000` is now a **per-request** guard. It can be lowered in the server environment to use smaller chunks. The model's token limit, `MAST_MAX_INPUT_TOKENS`, fixed taxonomy/examples, output reservation and safety margin all apply to classification, evidence and reconciliation requests. A limit smaller than the fixed instructions/headers is rejected before any request. Preview reports chunk ranges and estimated classification-input tokens; the total request count depends on evidence availability and reconciliation depth (normally two calls per chunk plus one reconciliation).

Chunking is an explicit SwarmLens extension, **not a claim of equivalence to the original whole-trace MAST evaluation**. The reconciliation model sees chunk summaries and localized evidence rather than the entire raw conversation. Information spanning distant chunks may be lost or misinterpreted by those summaries; the original recording and individual results stay available for inspection. Deterministic tests establish input coverage, budgeting, reference integrity and failure handling, not semantic classification accuracy.

### Expandable trait details

New reports from plugin version 0.3.0 include a separate evidence-localization request after the existing MAST judgment. This adds one model request when at least one judgment was parsed. The original classification prompt and verdicts are retained. The additional request explains each parsed trait and locates occurrences for positive traits, including supporting events and relevant later correction or counterevidence. It can report that a positive judgment could not be supported; it does not force an occurrence.

Click a trait row to expand its saved explanation and chronological context. Each occurrence lists its inclusive event span and counts of supporting, counterevidence, and context events. The initial view shows supporting and counterevidence entries with only the first cited message expanded; **Show full context** reveals the intervening events in their original order. Message cards render Markdown and LaTeX equations, show compact UTC timestamps, and offer an **Original text** toggle for the exact recorded content. Long messages scroll within the reader. Event links open the recorded event in the timeline and inspector. Opening details is read-only and never calls the judge. Older reports display an evidence-unavailable notice and the separately labeled overall summary; run a new analysis to obtain evidence.

Messages use the explorer's shared formatter, built from `frontend/message-format.js` using Markdown-it and KaTeX into a bundled local browser asset (`npm run build:message-format`, included in `npm run build`) that loads on first use. Equations use native MathML; no remote scripts, images, or fonts are loaded from message content. Recorded HTML remains text, and the original message is retained without rewriting or summarizing it.

The library returns this data in `analysis.output.evidence`, including the evidence version, request hash, model metadata, raw response, validation warnings, and details keyed by trait code. Event references are checked against the exact analyzed prefix, and positions are resolved from that input. Invalid references discard that trait's occurrences. Failure of the additional request preserves the original assessment with evidence marked unavailable. Raw transcript imports can cite their containing observation, but do not gain invented message identities.

`web/plugins/mast/details.py` presents evidence from the frozen trace artifact through the trait endpoint below; `web/plugins/mast/static/details.js` owns the disclosures. These explanations remain model assessments. Deterministic tests verify reference integrity and presentation, not semantic accuracy.

## Sub-API and registration

The normal CLI includes the MAST router in the main FastAPI application and its `/docs`:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/plugins/mast/capabilities` | Model, readiness, saved-trace mode, upstream revision |
| GET | `/api/plugins/mast/taxonomy` | Definitions, question labels, ambiguity notes |
| POST | `/api/plugins/mast/analyses` | Submit `{branch_id, cursor, completeness?}`; receive HTTP 202 and job ID |
| POST | `/api/plugins/mast/preview` | Check the same frozen snapshot and token budget without calling the judge |
| GET | `/api/plugins/mast/analyses?branch_id=...` | Most recent 50 jobs for this branch |
| GET | `/api/plugins/mast/analyses/{job_id}` | Job state and saved result |
| GET | `/api/plugins/mast/analyses/{job_id}/traits/{code}` | Saved trait explanation, occurrence spans, and original context text |

`completeness` is `unknown` (default), `complete`, or `partial`. A data boundary alone is not evidence of task termination. Jobs always analyze the submitted snapshot, even if its run later continues.

For an application-owned composition root:

```python
from swarm_lens.observability.mast import MastPlugin
from swarm_lens.observability.mast.judge import OpenAIMastJudge
from swarm_lens.observability.mast.service import MastJobs, MastService
from swarm_lens.web.plugins.mast import mast_extension
from swarm_lens.web.api import create_app

service = MastService(framework, MastPlugin(OpenAIMastJudge()),
                      MastJobs("data/mast.sqlite"), artifacts)
app = create_app(framework, artifacts, extensions=(mast_extension(service),))
```

MAST is an ordinary [web plugin](../../integration.md#write-a-web-plugin): everything it adds lives in `src/swarm_lens/web/plugins/mast/`, and its browser module (`static/index.js`) installs the Reports view and the **Analyze with MAST…** action through the same host API any plugin uses. Duplicate IDs/routes fail startup. Routes must use the plugin's namespace. The domain/application layers and CASPIAN remain independent of FastAPI.

## Provenance and limits

`tools/build_mast_assets.py` extracts prompt data without executing the notebook. Its bundled JSON lets installed wheels work without Git or runtime downloads. Each result records upstream commit/file hashes, plugin version, input digest, branch/cursor, configuration, prompt and trace hashes, model response, and usage. Exact prompt/trace artifacts and results persist locally.

The notebook's question list and definitions file **swap the names of 3.2 and 3.3**. Its example answer also includes obsolete codes **1.6 and 2.7**. We preserve the published prompt and question codes, expose both names in the taxonomy API, flag the ambiguity in result details, and do not silently remap categories. The extra codes are not scored.

Integration changes are explicit: Swarm Lens supplies its saved event projection, adds a provider timeout/output limit and `store=False`, chunks oversized input instead of silently truncating, and parses strictly. Missing or ambiguous answers stay `null`; unfinished responses become `needs_review`. Provider failures do not imply negative classifications. Raw text uploads remain limited to 200,000 characters. Each prepared chunk has a configurable 4,000,000-character guard plus a model-specific token budget for the **entire request prompt**; reconciliation inputs are checked too. The local `o200k_base` tokenizer estimates text tokens; the check also reserves output space and a framing margin. Its public vocabulary is downloaded on first use and cached locally.

Projection v2 preserves every event in order and replaces exact repeated strings with references to a shared text table. Canonically serialized JSON memory snapshots can reference those same strings. References retain every occurrence: repeated agent behavior is still visible at each event. `decode_trace` reconstructs the original projection, including the exact memory strings; tests compare both complete saved ACIArena examples and their prefixes. Original histories and raw model-call artifacts remain untouched. The lossless input projection itself contains no summaries or dropped turns; the upstream MAST judgment prompt template, definitions, and examples remain unchanged. Oversized inputs now use the chunked analysis and reconciliation extension described above. Plugin version 0.2.0 and each report's input summary identify this representation change; older reports retain their original judge and input.

Jobs persist in `mast.sqlite`, and completed assessments use the existing history-store analysis records. Unfinished jobs become `interrupted` after restart; paid requests are not automatically repeated. Start another analysis to retry. The initial executor uses FastAPI background tasks, at most four pending jobs, and **one server process**. Distributed execution is not implemented.

Responses are rendered as text. Integration tests verify persistence, parsing, and isolation, not classification accuracy or CASPIAN validity.
