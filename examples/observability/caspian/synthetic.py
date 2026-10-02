"""Self-contained numeric trace; no model calls, embeddings or attack ground truth."""
import json

import numpy as np

from swarm_lens.observability.caspian import CHANNELS, Caspian, CaspianConfig, ChannelEvent, Turn

AGENTS = ('planner', 'researcher', 'reviewer', 'writer')
EDGES = tuple(zip(AGENTS[:-1], AGENTS[1:]))


def turns(count=120, seed=21):
    rng = np.random.default_rng(seed)
    for index in range(1, count + 1):
        events = []
        for source, target in EDGES:
            for channel in CHANNELS:
                u, noise, independent = rng.normal(size=3)
                v = independent if index < 60 else u + .2 * noise
                events.append(ChannelEvent(source, target, channel, (float(u),), (float(v),)))
        yield Turn(index, tuple(events))


def run():
    method = Caspian(AGENTS, EDGES, CaspianConfig(), feature_schema='synthetic-paired-numeric-v1')
    results = []
    for turn in turns():
        result = method.update(turn)
        results.append(result)
        if method.finished:
            break
    return {'method': method.describe(), 'processed_turns': len(results),
            'interpretation': 'Synthetic dependence shift at turn 60. Default literal rules alert at warmup (turn 8), before that shift: an implementation limitation, not evidence of an attack.',
            'last_turn': results[-1]}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, allow_nan=False))
