import json
import pytest
from examples.aciarena.benchmark import save, summarize
from examples.aciarena.matrix import (PersistentBudget, completed_case, matrix_cases,
                                     next_attempt, output_lock, prepare_manifest)
from examples.aciarena.recording import BudgetExceeded
from examples.aciarena.upstream import load_tasks, ROOT


def test_budget_survives_resume_and_charges_uncertain_requests(tmp_path):
    path = tmp_path/'budget.json'
    budget = PersistentBudget(path, 100, 5)
    budget.reserve(30, 10)
    budget.complete(chat_input=20, chat_output=5)
    budget.reserve(30, 10)  # simulate interruption while request is in flight
    restored = PersistentBudget(path, 100, 5)
    assert restored.tokens == 65
    assert restored.requests == 2
    assert restored.uncertain_tokens == 40
    with pytest.raises(BudgetExceeded):
        restored.reserve(36)
    assert PersistentBudget(path, 100, 5).tokens == 65  # never double-charge recovery


def test_resume_rejects_changed_protocol_and_locks_output(tmp_path):
    manifest = {'model':'first', 'cases':['one']}
    prepare_manifest(tmp_path, manifest)
    prepare_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='protocol'):
        prepare_manifest(tmp_path, {'model':'second','cases':['one']})
    with output_lock(tmp_path):
        with pytest.raises(ValueError, match='Another process'):
            with output_lock(tmp_path):
                pass


def test_resume_skips_complete_cases_and_preserves_partial_attempts(tmp_path):
    case = {'id':'math-000-benign','task':'math-000','condition':'benign'}
    first = next_attempt(tmp_path, case)
    first.mkdir()
    (first/'trace.jsonl').write_text('partial evidence')
    assert completed_case(tmp_path, case) is None
    second = next_attempt(tmp_path, case)
    assert second != first
    second.mkdir()
    result = {'status':'complete','task':case['task'],'condition':case['condition'],
              'history':{'replay_matches':True}}
    save(second/'result.json', result)
    assert completed_case(tmp_path, case) == result
    assert (first/'trace.jsonl').read_text() == 'partial evidence'


@pytest.mark.skipif(not (ROOT/'aciarena').exists(), reason='Optional upstream submodule absent')
def test_complete_math_matrix_has_unique_117_cases():
    cases = matrix_cases(load_tasks(39)[0])
    assert len(cases) == len({c['id'] for c in cases}) == 117
    for condition in ('benign','name_disclosure','location_disclosure'):
        assert sum(c['condition'] == condition for c in cases) == 39


def test_summary_includes_both_attacks():
    runs = [{'status':'complete','condition':condition,'alert':None,'attack_success':True,
             'first_downstream_marker':None} for condition in ('name_disclosure','location_disclosure')]
    assert summarize(runs)['attacked_runs'] == 2
    assert summarize(runs)['attack_successes'] == 2
