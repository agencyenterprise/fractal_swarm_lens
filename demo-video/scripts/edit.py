"""The edit decision list for the Swarm Lens demo (read by build.py).

Each segment plays pieces of one recorded shot: (source in, source out, seconds on screen). Pieces that
touch ramp smoothly between speeds; a gap between pieces is a jump cut. Segment lengths are whole
beats at 120 BPM, so every cut lands on the music grid. Times in camera moves, captions and highlights
are seconds into the segment. See storyboard.md for why each choice was made.
"""
from build import Caption, Highlight, Move

BEAT = 0.5


def card(shot, seconds, enter="whip"):
    return dict(shot=shot, pieces=[(0.0, seconds, seconds)], enter=enter)


EDIT = [
    # Act 0: the problem, in swarmchasing.com's words, before the platform appears.
    card("p1-quote", 4.5, enter="cut"),
    card("p2-quote", 4.0, enter="fade"),
    card("p3-need", 4.0, enter="fade"),

    # Act 1: the platform and the example scenario.
    card("01-title", 3.5, enter="zoom"),
    card("c0-setup", 3.5, enter="zoom"),
    card("c0-question", 2.0, enter="fade"),

    # Act 1b: any dataset, through one small adapter; built for long runs.
    card("d1-data", 3.0),
    card("d2-adapter", 6.0, enter="wipe"),
    dict(shot="02-import", enter="zoom",
         pieces=[(0.9, 2.0, 0.5), (2.0, 4.4, 0.8), (4.4, 8.0, 1.2)],
         camera=[Move(1.3, 1.7, 1.45, "dialog")],
         captions=[Caption("Paste a transcript or import a run bundle", 0.15, 2.4)]),
    dict(shot="02-datasets", enter="cut",
         pieces=[(0.6, 1.6, 0.5), (1.6, 2.9, 1.1), (2.9, 4.9, 1.0), (4.9, 7.3, 1.4), (7.3, 9.8, 0.8), (9.8, 11.4, 0.7)],
         camera=[Move(0.4, 0.7, 1.4, "list"), Move(2.45, 2.75, 1.0)],
         captions=[Caption("ACIArena debates", 0.1, 1.5),
                   Caption("Messageboard runs, from a different source", 1.65, 4.0),
                   Caption("Open medicine-004, attacked condition", 4.15, 5.4)]),
    card("l1-long", 4.0),

    # Act 2: investigate the run (ACIArena medicine-004, Debater 0 prompted to argue for (B)).
    card("c2-replay", 1.0),
    dict(shot="03-scrub", enter="zoom",
         pieces=[(0.7, 4.7, 1.2), (6.9, 10.9, 0.9), (12.0, 13.3, 0.4)],
         captions=[Caption("Scrub through every message", 0.15, 2.4)]),
    dict(shot="c6-views", pieces=[(0.0, 2.5, 2.5)], enter="whip"),
    dict(shot="14-montage", enter="zoom",
         pieces=[(2.4, 3.0, 0.6), (3.75, 4.45, 0.7), (6.5, 7.15, 0.7)],
         captions=[Caption("Blast radius, phrase spread, activity", 0.1, 1.95)],
         sounds=[("tick", 0.6), ("tick", 1.3)]),
    dict(shot="04-echo", enter="cut",
         pieces=[(0.7, 1.2, 0.3), (1.2, 3.2, 1.2), (3.2, 4.2, 0.5), (4.2, 9.1, 3.5)],
         camera=[Move(3.3, 3.8, 1.35, "attackerLine")],
         captions=[Caption("Influence: who reads whom", 0.1, 1.45),
                   Caption("Echo: share of each answer copied from what it read", 1.6, 3.35),
                   Caption("Debater 0 plateaus near 55%, the others near 100%", 3.55, 5.45)],
         highlights=[Highlight("attackerLine", 3.5, 5.45, "Debater 0")]),
    dict(shot="05-fork", enter="wipe",
         pieces=[(0.6, 2.2, 2.0)],
         camera=[Move(1.0, 1.35, 1.3, "message")],
         captions=[Caption("Debater 0's first message argues for (B)", 0.6, 1.95)]),

    # Act 3: intervene, and run it forward for real.
    card("c3-fork", 1.0),
    dict(shot="05-fork", enter="zoom",
         pieces=[(2.2, 4.6, 0.8), (4.6, 5.3, 0.8), (5.3, 9.0, 1.1), (9.0, 10.4, 0.8), (10.4, 14.0, 2.5)],
         camera=[Move(0.85, 1.15, 1.6, "effect"), Move(2.0, 2.3, 1.3, "prompt"), Move(2.9, 3.2, 1.0)],
         captions=[Caption("Replace only Debater 0's system prompt", 0.9, 2.75),
                   Caption("Continue the run from that event (CrewAI)", 3.7, 5.95)],
         highlights=[Highlight("liveStatus", 3.6, 5.95, "Running", tone="accent", pad=10)]),
    card("c4-compare", 1.0),
    dict(shot="06-payoff", enter="zoom",
         pieces=[(0.5, 1.6, 0.6), (1.6, 5.8, 3.4)],
         camera=[Move(0.6, 1.1, 1.3, (615, 450))],
         captions=[Caption("Same task, one prompt changed: (B) → (A)", 1.3, 3.95)],
         highlights=[Highlight("answerB", 0.9, 3.95, "Recorded"),
                     Highlight("answerA", 1.2, 3.95, "Forked", tone="accent")]),

    # Act 4: plugins. Built-in first, then your own (docs, coding agent, API), then the ones in development.
    card("g0-builtin", 5.5),
    dict(shot="09-mast", enter="zoom",
         pieces=[(0.9, 2.4, 0.5), (2.4, 4.6, 0.7), (6.9, 7.7, 0.4), (7.7, 9.25, 2.2),
                 (9.25, 10.3, 0.6), (10.3, 11.6, 1.8), (11.6, 12.15, 1.3)],
         camera=[Move(0.05, 0.35, 1.45, "menu"), Move(0.55, 0.85, 1.4, "dialog"), Move(1.15, 1.35, 1.0),
                 Move(4.4, 4.75, 1.25, (660, 560)), Move(6.15, 6.5, 1.6, (686, 770))],
         captions=[Caption("MAST: 14 failure modes in 3 categories", 0.1, 3.75),
                   Caption("Why it was flagged, with supporting events", 4.45, 6.05),
                   Caption("Each finding cites events in the trace", 6.25, 7.45),
                   Caption("Cemri et al., 2025 \u00b7 arXiv:2503.13657 \u00b7 judge here: gpt-5.5", 0.1, 7.45, place="note")]),
    card("g2-docs", 6.5, enter="wipe"),
    dict(shot="10-coding-agent", pieces=[(0.0, 6.5, 6.5)], enter="wipe"),
    card("13-api", 2.5),
    card("g6-method-plugins", 8.5),
    card("c7-comment", 1.0),
    dict(shot="15-comment", enter="zoom",
         pieces=[(0.8, 1.8, 0.6), (1.8, 4.4, 1.6), (4.4, 6.0, 1.3), (7.4, 8.4, 0.9), (9.6, 10.6, 0.6), (10.6, 11.9, 1.0)],
         camera=[Move(0.45, 0.75, 1.6, "composer"), Move(2.0, 2.3, 1.0), Move(2.45, 2.75, 1.5, "tooltip22"),
                 Move(3.5, 3.6, 1.5, "tooltip199"), Move(4.3, 4.6, 1.0)],
         captions=[Caption("Flag moments, then share the run with your team", 0.1, 5.85)]),
    dict(shot="16-chat", pieces=[(0.0, 4.5, 4.5)], enter="whip"),
    card("c8-live", 1.0),
    dict(shot="05-fork", enter="zoom",
         pieces=[(14.0, 25.7, 3.0)],
         captions=[Caption("Watch runs live as they happen", 0.15, 2.8)],
         highlights=[Highlight("liveStatus", 0.3, 2.8, "Running", tone="accent", pad=10)]),
    card("12-end", 3.5, enter="zoom"),
]

# Music arrangement, by segment: the problem cards carry only the pad; drums enter with "Bring your own data",
# claps lift the plugin act,
# and everything but the pad stops on the end card's hit.
MUSIC = {"drums": ("d1-data", "12-end"), "lift": ("g0-builtin", "12-end"), "end": "12-end"}
