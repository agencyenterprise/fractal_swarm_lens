# Swarm Lens demo video: storyboard (v7, main submission, 2:06)

v7 is the hackathon's main submission video (the form asks for 2 to 3 minutes). It keeps v6's style, pacing, captions, audio pipeline and honesty rules. It adds four things v6 did not cover:
- the problem it answers;
- that any dataset works;
- that long, text-heavy runs are handled efficiently;
- a fuller account of plugins, with the MAST reference.

## Sources read before writing the copy
- **swarmchasing.com (read; quoted):**
  - The tagline: "Building the tools we wished we had for the Hugging Face incident".
  - Ryan Greenblatt's line: "We don't have good approaches for understanding/overseeing the activity and aims of AI 'swarms'."
  - "Society lacks the urgently needed tools to make sense of thousands of agents coordinating, as in the OpenAI-Hugging Face incident and the German Wiki incident."
  - Its project areas include "information spread tracing" and "multi-agent analysis".
- **Cemri et al., "Why Do Multi-Agent LLM Systems Fail?", arXiv:2503.13657 (abstract read):**
  - MAST is the Multi-Agent System Failure Taxonomy: 14 failure modes in 3 categories.
  - It came from 150 traces analyzed with expert annotators (κ = 0.88), and it has an LLM-as-a-judge pipeline.
  - Our judge is gpt-5.5, not the paper's model. The screen says so.
- **PR #6 (storage):**
  - Long texts are stored once by SHA-256 and compressed with zlib. Snapshots are deltas.
  - Synthetic benchmark (seed 7, n = 1): 30k messages went from 11,970 MB to 119 MB. Storage per message stayed flat at 4.0 KB from 10k to 100k messages.
  - The PR states one limit: a memory that keeps growing without resets is still quadratic. So the video does not claim linear growth for every run. It states the technique, plus one labeled number.
- **The code:**
  - `Source.facts() -> Iterable[Fact]` is the adapter contract (`src/swarm_lens/application/ports.py`). `examples/aciarena/sample_source.py` yields `Fact(...)` records.
  - PR #7 (merged): `WebExtension(id, router, manifest, assets)` and `install(host, manifest)`, with `host.addAction` and `host.registerVisualization`. MAST is the reference plugin (`src/swarm_lens/web/plugins/mast/`).
  - The timeline draws only the visible range when you scroll (`timeline.js`). The transcript loads full texts as entries come on screen (`transcript.js`, IntersectionObserver).

## Story
1. **The problem** (about 12 s): the swarmchasing.com framing, quoted, then the concrete failure we will chase: one agent pulling the others. This comes before the platform appears.
2. **Title and the example scenario** (v6 opening).
3. **Bring your own data** (about 17 s): the primitives, real adapter code, two different data sources in the run picker, then open the protagonist run.
4. **Built for long runs** (about 4 s): the technique as the headline, one number labeled "synthetic benchmark".
5. **Investigate, fork, compare** (v6, unchanged): the protagonist story, (B) → (A) for real.
6. **Plugins** (about 37 s): the built-in plugins (MAST, with its reference, and the views), then the ⋯ menu and MAST, whose citation stays small on screen. Then your own: the documented plugin API, the coding agent and the HTTP API, and finally the method plugins in development.
7. **Comment and share, live monitoring, end card** (v6).

## Shot list
| Seg | Len | Content | Kind |
|---|---|---|---|
| P1 | 4.5 | Quote card: "We don't have good approaches for understanding/overseeing the activity and aims of AI 'swarms'." (Ryan Greenblatt, via swarmchasing.com) | CARD (quote) |
| P2 | 4.0 | "Society lacks the urgently needed tools to make sense of thousands of agents coordinating." (swarmchasing.com) | CARD (quote) |
| P3 | 4.0 | "When one agent goes wrong, it can pull the others with it." / "We need to see where, and test what would have stopped it." | CARD |
| T | 3.5 | Logo, then the title (v6) | CARD |
| S | 3.5 + 2.0 | Example scenario, then the question (v6) | CARD |
| D1 | 3.0 | "Bring your own data" / "Any agentic dataset maps to agents, channels, messages, memory, tool calls and environment." | CARD |
| D2 | 6.0 | Code: `Source.facts()` contract, plus verbatim `yield Fact(...)` lines from the ACIArena adapter | CARD (real code) |
| D3 | 2.5 | Import trace dialog | REAL |
| D4 | 5.5 | Run picker: ACIArena debates and Messageboard runs, then a Messageboard run opens, then medicine-004 opens | REAL (new) |
| L | 4.0 | "Built for long, text-heavy runs" / technique lines / "Synthetic benchmark, 30k messages: 11,970 MB → 119 MB" | CARD |
| Investigate | 15.5 | Replay, views, Influence and Echo, the first message (v6) | REAL |
| Fork | 12.0 | Fork, Create and run, Compare (B) → (A) (v6) | REAL |
| G0 | 5.5 | "Built-in plugins included": MAST failure analysis, Activity summary, the views; MAST reference (Cemri et al., 2025, arXiv:2503.13657) | CARD |
| G4 | 7.5 | ⋯ menu Plugins section, MAST dialog, report, 2.6, evidence; the reference stays small on screen | REAL |
| G2 | 6.5 | "Write your own plugin. It is documented.": verbatim excerpts of `docs/integration.md` | CARD (real docs) |
| G5 | 6.5 + 2.5 | Coding agent, then the HTTP API (v6) | CARD |
| G6 | 8.5 | "Method plugins we are building" | CARD |
| Share | 11.5 | Comments, export, chat (v6) | REAL + CARD |
| Live | 4.0 | Live monitoring (v6, real) | REAL |
| End | 3.5 | End card (v6) | CARD |

Total length is 2:06 (see `scripts/edit.py`). Cuts stay on the 120 BPM beat grid.
