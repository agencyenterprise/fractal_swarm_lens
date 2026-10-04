from dataclasses import asdict
import json

import pytest

from tests.support.caspian_branch import NumericHistory, NumericSource, factory, run
from swarm_lens import Fact, Framework, PluginService
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.observability import ObservabilityPlugin
from swarm_lens.observability.caspian import Caspian, CaspianConfig, ChannelEvent, Turn


def test_nested_branch_example_and_persisted_provenance(tmp_path):
    output = run(tmp_path)
    parent, child = output['parent_analysis'], output['nested_branch_analysis']
    assert parent['output']['report']['turns'] == child['output']['report']['turns']
    assert parent['input_digest'] != child['input_digest']
    assert parent['output']['report']['method']['config'] == asdict(CaspianConfig())
    assert parent['output']['report']['adapter']['version'] == '1'
    json.dumps(output, allow_nan=False)
    store = SQLiteHistory(tmp_path / 'history.sqlite')
    assert store.analyses(child['branch_id'], child['cursor'])[0]['output'] == child['output']


def test_cursor_isolation_fresh_estimator_and_future_parent_exclusion(tmp_path):
    plugin = ObservabilityPlugin('caspian', Caspian.version, factory, NumericHistory())
    framework = Framework(SQLiteHistory(tmp_path / 'history.sqlite'))
    plugins = PluginService(framework, (plugin,))
    parent = framework.create_run('test')
    framework.ingest(parent.id, NumericSource())
    child = framework.fork(parent.id, 10, 'historical')
    first = plugins.analyze(plugin.id, parent.id, 1, 10)
    plugins.analyze(plugin.id, parent.id, 1, 16)
    second = plugins.analyze(plugin.id, child.id, 1, 10)
    third = plugins.analyze(plugin.id, parent.id, 1, 10)
    assert first['output'] == second['output'] == third['output']
    assert len(first['output']['report']['turns']) == 6
    assert first['output']['report']['turns'][-1]['evidence']['sample_counts'][0][1][0] == 6


def test_another_method_uses_same_extension_without_caspian_assumptions(tmp_path):
    class Count:
        id, version, finished = 'count', '1', False
        def describe(self): return {'meaning': 'test method'}
        def update(self, observation): return {'count': len(observation.events)}
    plugin = ObservabilityPlugin('count', '1', lambda config: Count(), NumericHistory())
    framework = Framework(SQLiteHistory(tmp_path / 'history.sqlite'))
    plugins = PluginService(framework, (plugin,))
    branch = framework.create_run('count')
    framework.ingest(branch.id, NumericSource())
    result = plugins.analyze(plugin.id, branch.id, 1, 16)
    assert result['output']['report']['turns'] == [{'count': 12}] * 12


@pytest.mark.parametrize('kwargs', [{'epsilon': 0}, {'history_alpha': 2}, {'shrinkage': -1},
                                    {'min_samples': 1}, {'top_k': 0}, {'path_budget': True},
                                    {'normalization': 'mystery'}, {'persistence': 'mystery'},
                                    {'missing_evidence': 'mystery'}])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        CaspianConfig(**kwargs)


def test_turn_order_invalid_input_and_declared_observation_contract():
    method = Caspian(('a', 'b'), [('a', 'b')], feature_schema='numeric-v1', observed_channels=('comm',))
    with pytest.raises(ValueError):
        method.update(Turn(2, ()))
    assert not method.estimator.estimates
    with pytest.raises(ValueError):
        method.update(Turn(1, (ChannelEvent('a', 'b', 'exec', (1,), (2,)),)))
    method.update(Turn(1, ()))
    with pytest.raises(ValueError):
        method.update(Turn(1, ()))
    assert method.detector.last_turn == 1


def test_end_to_end_synthetic_exposes_startup_alert():
    from tests.support.caspian_synthetic import run
    result = run()
    assert result['processed_turns'] == 120
    assert result['last_turn']['alert']['confirmation_turn'] < 60
    assert result['last_turn']['alert']['attribution_status'] == 'complete'
    assert result['last_turn']['evidence']['ready_triplets'] == 12
    assert result['last_turn']['turn'] == 120
    assert result['last_turn']['evidence']['sample_counts'][0][1][0] == 120
    assert not result['last_turn']['new_alert']
    json.dumps(result, allow_nan=False)


def test_child_observations_change_only_child_analysis(tmp_path):
    plugin = ObservabilityPlugin('caspian', Caspian.version, factory, NumericHistory())
    framework = Framework(SQLiteHistory(tmp_path / 'history.sqlite'))
    plugins = PluginService(framework, (plugin,))
    parent = framework.create_run('suffix')
    framework.ingest(parent.id, NumericSource())
    child = framework.fork(parent.id, 11, 'new observed continuation')  # Seven observed turns.
    before = plugins.analyze(plugin.id, parent.id, 1, 12)

    class Continuation:
        def facts(self):
            pair = asdict(ChannelEvent('planner', 'researcher', 'comm', (100.,), (-100.,)))
            yield Fact('observation.recorded', {'type': 'paired_numeric_turn', 'turn': 8,
                                               'pairs': [pair]}, '2026-01-01T00:00:01+00:00')

    framework.ingest(child.id, Continuation())
    changed = plugins.analyze(plugin.id, child.id, 1, 12)
    after = plugins.analyze(plugin.id, parent.id, 1, 12)
    assert before['output'] == after['output']
    assert changed['output']['report']['turns'][-1]['influence_tensor'] != before['output']['report']['turns'][-1]['influence_tensor']
    assert changed['output']['report']['turns'][-1]['evidence']['ready_triplets'] == 1
