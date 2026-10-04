"""Application mapping, historical replay, nested forks, and an explicit intervention."""
from dataclasses import asdict
from pathlib import Path

from swarm_lens import Fact, Framework, PluginService
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.observability import ObservabilityPlugin
from swarm_lens.observability.caspian import Caspian, CaspianConfig, ChannelEvent, Turn
from .caspian_synthetic import AGENTS, EDGES, turns


class NumericHistory:
    id, version = 'paired-numeric-observations', '1'

    def describe(self):
        return {'schema': 'synthetic-paired-numeric-v1', 'turn_boundary': 'one explicit observation fact',
                'limitations': 'Synthetic numeric observations, not model internals or attack labels'}

    def observations(self, history):
        for event in history:
            if event.kind == 'observation.recorded' and event.data.get('type') == 'paired_numeric_turn':
                yield Turn(event.data['turn'], tuple(ChannelEvent(**item) for item in event.data['pairs']))


def factory(config):
    return Caspian(AGENTS, EDGES, CaspianConfig(**config), feature_schema='synthetic-paired-numeric-v1')


class NumericSource:
    def facts(self):
        at = '2026-01-01T00:00:00+00:00'
        for agent in AGENTS:
            yield Fact('agent.added', {'id': agent, 'name': agent}, at)
        for turn in turns(12):
            yield Fact('observation.recorded', {'type': 'paired_numeric_turn', 'turn': turn.index,
                                               'pairs': [asdict(e) for e in turn.events]}, at)


def run(root):
    plugin = ObservabilityPlugin('caspian', Caspian.version, factory, NumericHistory())
    framework = Framework(SQLiteHistory(Path(root) / 'history.sqlite'))
    plugins = PluginService(framework, (plugin,))
    recorded = framework.create_run('CASPIAN synthetic application')
    framework.ingest(recorded.id, NumericSource())
    # Four agent creation events + six observed turns, safely before default warmup.
    cursor = 10
    parent = plugins.analyze(plugin.id, recorded.id, 1, cursor)
    child = framework.fork(recorded.id, cursor, 'Check source evidence')
    framework.intervene(child.id, 'agent.updated', {
        'id': 'reviewer', 'system_prompt': 'Verify source evidence before passing claims downstream.'
    }, expected_head=cursor)
    nested = framework.fork(child.id, cursor + 1, 'Nested verification')
    branch = plugins.analyze(plugin.id, nested.id, 1, cursor + 1)
    assert parent['output']['report']['turns'] == branch['output']['report']['turns']
    assert parent['input_digest'] != branch['input_digest']
    return {'parent_analysis': parent, 'nested_branch_analysis': branch,
            'interpretation': 'The recorded prompt intervention adds no runtime evidence; scores stay unchanged.'}

