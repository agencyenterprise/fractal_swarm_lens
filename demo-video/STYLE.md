# Style guide for the demo video

These rules come from the review rounds on this video. Follow them when you change it.

## Audience and tone
- The audience is AI researchers judging a hackathon. Write in a researcher's register: precise, factual, no hype.
- No sales drama: no "secretly", "liar", "rogue", no consumer-style hooks. State the setup plainly, for example "ACIArena LLM debate: 3 debaters + aggregator. Debater 0 is prompted to argue for a wrong answer."
- Start with the problem and motivation before showing the product. Use swarmchasing.com's own framing and quote it faithfully.
- Every caption must make sense to a first-time viewer with no context. Say what is on screen.

## Honesty
- Only real footage of the product. No mocked screens presented as the product and no "coming next" labels; the video should be timeless.
- Cite sources only after reading them. MAST: Cemri et al., "Why Do Multi-Agent LLM Systems Fail?", arXiv:2503.13657. Our MAST judge is gpt-5.5, not the paper's model.
- Benchmark numbers come from PR #6 and are labeled "synthetic benchmark". Prefer stating the technique over numbers.
- Plugins in development are listed by name and what they show, never with detection claims (several detection results were negative).
- The fork payoff ((B) → (A) on medicine-004) is a real run; do not claim it always works.

## What to say
- Data agnostic: any agentic dataset maps to agents, channels, messages, memory, tool calls and environment through one small adapter.
- Built for long, text-heavy runs: each text stored once and compressed, snapshots store only changes.
- Plugins, explained at length: built-in plugins ship with it (MAST, Activity, and the views Lanes, Influence, Blast radius, Phrase spread, Echo); on top of that you can write your own easily, it is documented, and a plugin can add analyses, views, UI and interventions. Your coding agent (Claude Code, Codex, any CLI) can write them. Same operations from the UI or the HTTP API.
- Keep all visualizations in one section ("Many lenses on the same run"); do not fragment it.
- Do not use "No LLM judge needed"; LLM-judged views are part of the plan.

## Visuals
- Light mode everywhere (force it: colorScheme light and localStorage `swarm-lens:theme=light`).
- No colored accent bars on captions or cards.
- Captions over recordings: one line, at most about 8 words, dark pill (near-black, about 92% opacity) with white text, short slide-in, never covering the UI element being shown.
- Section title cards are short and bold; no "·" separator dots on cards.
- The logo is the animated GIF `assets/branding/swarm-lens-robot-factory-v1/swarm-lens-observation.gif` (320 px). Scale only by integer factors with nearest-neighbor so pixels stay crisp; never fill the screen with it.

## Pacing
- Speed-ramp waits, cut dead frames, shots of 1.5 to 3 s, punch-in zooms on the element that matters, cut on the music beat.
- Give explanations room: the plugin section (about 30 to 40 s), MAST (about 6 to 8 s) and comments and sharing (about 8 to 12 s) must be readable. Do not overcorrect into slowness elsewhere.
- Every caption stays on screen long enough to read once (about 1.2 s per 5 words).

## Audio
- Clicks and key sounds land on the recorded action timestamps; whooshes start just before cuts; the music bed is generated in code (no downloaded audio).
- Lessons from fixing a crackle: let synthesized notes ring out to silence instead of cutting them mid-wave; apply one measured gain change, not dynamic loudness normalization; filter long effects in one pass, not in chunks. Find the cause by measurement; never mask problems with fades or a limiter.
- Targets: -16 LUFS, true peak at or below -1.5 dBTP, AAC 48 kHz stereo at 256 kbps.
