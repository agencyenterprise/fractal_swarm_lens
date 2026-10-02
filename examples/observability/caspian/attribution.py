"""Inspect exact role/spine calculations from supplied influence, bypassing LI-CTE."""
import json

import numpy as np

from swarm_lens.observability.caspian import CaspianConfig
from swarm_lens.observability.caspian.topology import attribute, make_snapshot, topology


def run():
    agents, mask = topology(('source', 'relay', 'worker', 'sink'),
                           [('source', 'relay'), ('relay', 'worker'), ('worker', 'sink')])
    first, second = np.zeros((4, 4, 4)), np.zeros((4, 4, 4))
    first[0, 1, 0], first[1, 2, 1], first[2, 3, 2] = 8, 2, 1
    second[0, 1, 0], second[1, 2, 1], second[2, 3, 2] = 1, 9, 7
    config = CaspianConfig(top_k=6)
    result = attribute([make_snapshot(t, mask, config) for t in (first, second)], mask, agents, config)
    return {'input_kind': 'hand-specified influence tensors; not LI-CTE estimates or detected attacks',
            'attribution': result}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, allow_nan=False))
