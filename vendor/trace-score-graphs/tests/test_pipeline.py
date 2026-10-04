import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trace_score_graphs import dataset
from trace_score_graphs.client import validate
from trace_score_graphs.common import save
from trace_score_graphs.evidence import clip, records
from trace_score_graphs.parsers import normalize
from trace_score_graphs.pipeline import export
from trace_score_graphs.rubrics import LAYERS, questions


def row(text, framework='AG2', label=None):
    labels = {c: 0 for c in dataset.LABELS}
    if label:
        labels[label] = 1
    return {'trace': {'trajectory': text, 'key': 'test', 'index': 0},
            'trace_id': 0, 'mas_name': framework, 'benchmark_name': 'test',
            'mast_annotation': labels}


class ExtractionTests(unittest.TestCase):
    def test_repr_not_executed_and_roles_preserved(self):
        text = repr({'content': ['What is 2 + 2?'], 'name': 'planner', 'role': 'user'})
        text += ' ' + repr({'content': ['Four.'], 'name': 'solver', 'role': 'assistant'})
        c = normalize(row(text), 'x')
        self.assertEqual([e['actor'] for e in c['events']], ['planner', 'solver'])
        self.assertEqual(c['events'][0]['recipients'], ['solver'])
        self.assertEqual(c['events'][0]['delivery_basis'], 'inferred_two_party_transcript')

    def test_chatdev_ignores_summary_and_receiver_system_prompt(self):
        t = ('**task_prompt**: Make a game.\n'
             '[2025-31-03 19:00:00 INFO] Reviewer: **Reviewer<->Coder on : Review, turn 0**\n'
             '[ChatDev company\nYou are the Coder.]\n\nFix the loop.\n'
             '[2025-31-03 19:00:01 INFO] **[Seminar Conclusion]**:\nFix the loop.\n'
             '[2025-31-03 19:00:02 INFO] Coder: **Coder<->Reviewer on : Modify, turn 0**\nFixed.\n')
        c = normalize(row(t, 'ChatDev'), 'x')
        self.assertEqual(len(c['events']), 2)
        self.assertEqual(c['events'][0]['actor'], 'Reviewer')
        self.assertEqual(c['events'][0]['content'], 'Fix the loop.')

    def test_ag2_benchmark_answer_not_in_task_or_events(self):
        t = ('problem_statement: What is 2 + 2?\nother_data:\n  correct: True\n'
             '  seed_solution: SECRET_GOLD_ANSWER\ntrajectory:\n'
             '      content: What is 2 + 2?\n      role: assistant\n      name: proxy\n'
             '      content: Four\n      role: user\n      name: solver\n')
        c = normalize(row(t), 'x')
        self.assertNotIn('SECRET_GOLD_ANSWER', json.dumps(records(c)))

    def test_future_and_missing_delivery_are_explicit(self):
        t = '---------- user ----------\nQuestion\n---------- A ----------\nHypothesis\n---------- B ----------\nReply\n'
        c = normalize(row(t, 'Magentic'), 'x')
        p = records(c)
        first = next(x for x in p if x['scope'] == 'message')
        self.assertNotIn('response', first['state'])
        self.assertNotIn('Hypothesis', json.dumps(first['state']))
        interaction = next(x for x in p if x['scope'] == 'interaction')
        self.assertEqual(interaction['available_at'], 1)
        self.assertEqual(interaction['delivery_basis'], 'temporal_candidate_unconfirmed_exposure')

    def test_broadcast_does_not_fabricate_recipients(self):
        t = "[2025-01-01 00:00:00] FROM: Human TO: {'<all>'}\nCONTENT:\nTask\n" \
            '[2025-01-01 00:00:01] NEW MESSAGES:\nSimpleCoder: Code\n'
        c = normalize(row(t, 'MetaGPT'), 'x')
        self.assertEqual(c['events'][0]['recipients'], [])

    def test_utf8_truncation_is_auditable(self):
        result = clip('🤖' * 200, 101)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['original_bytes'], 800)
        self.assertLessEqual(result['retained_bytes'], 101)

    def test_escaped_code_respects_serialized_budget(self):
        result = clip('\\"\n' * 2000, 1000)
        self.assertLessEqual(len(json.dumps(result['text'], ensure_ascii=False).encode()), 1000)

    def test_framework_metadata_mismatch_is_reported(self):
        text = ('**ChatDev Starts**\n**task_prompt**: Task\n'
                '[2025-01-01 01:00:00 INFO] Coder: **Coder<->Reviewer on : Coding, turn 0**\nCode')
        c = normalize(row(text, 'HyperAgent'), 'x')
        self.assertEqual(c['adapter'], 'ChatDev')
        self.assertEqual(c['source_framework'], 'HyperAgent')
        self.assertIn('framework_metadata_disagrees_with_log_format', c['coverage']['flags'])

    def test_openmanus_tool_call_matches_recorded_result(self):
        t = ("2025-04-01 01:00:00.000 | INFO | app.agent.toolcall:think:86 - Tools being prepared: ['editor']\n"
             '2025-04-01 01:00:00.001 | INFO | app.agent.toolcall:think:89 - Tool arguments: {"command":"view"}\n'
             "2025-04-01 01:00:01.000 | INFO | app.agent.toolcall:act:150 - Tool 'editor' completed its mission! Result: File contents\n")
        c = normalize(row(t, 'OpenManus'), 'x')
        self.assertEqual(c['events'][0]['recipients'], ['tool:editor'])
        relations = [p for p in records(c) if p['scope'] == 'interaction']
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]['delivery_basis'], 'logged_tool_selection')


class StorageTests(unittest.TestCase):
    def test_missing_decisions_are_null_in_nine_separate_graphs(self):
        t = repr({'content': ['Question'], 'name': 'A'}) + ' ' + repr({'content': ['Answer'], 'name': 'B'})
        c = normalize(row(t), 'c')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            save(root / 'active-run.json', {'run_id': 'r'})
            run = root / 'runs/r'
            save(run / 'schemas/c.json', c)
            (run / 'evidence').mkdir()
            (run / 'evidence/c.jsonl').write_text('\n'.join(json.dumps(p) for p in records(c)))
            result = export(root)
            self.assertEqual(result['graphs'], 9)
            self.assertGreater(result['missing_packets'], 0)
            self.assertFalse(result['complete'])
            g = json.loads((run / 'graphs/uptake/c.json').read_text())
            self.assertIsNone(g['edge_measurements'][0]['weight'])
            self.assertEqual(g['edge_measurements'][0]['status'], 'missing_decision')
            self.assertIsNone(g['aggregation'])

    def test_all_nine_questions_have_explicit_rubrics(self):
        allq = questions('message') | questions('interaction')
        self.assertEqual(len(LAYERS), 9)
        for name in LAYERS:
            self.assertEqual(allq[name]['type'], 'score')
            self.assertEqual(len(allq[name]['criteria']), 5)
            self.assertIn(name + '__status', allq)

    def test_invalid_api_response_not_accepted(self):
        with self.assertRaises(ValueError):
            validate({'model': 'jev', 'answers': {}, 'usage': {'cost': 0}}, questions('message'))

    def test_selection_disjoint_and_unknown_not_clean(self):
        rows = [row('clean unique')]
        for i, label in enumerate(dataset.LABELS):
            r = row('unique-' + label, label=label)
            r['trace_id'] = i + 1
            rows.append(r)
        unknown = row('unknown')
        unknown['mast_annotation']['1.1'] = None
        rows.append(unknown)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'dataset.json'
            save(path, rows)
            with patch.object(dataset, 'download', return_value=path):
                m = dataset.sample(d, count=1)
            self.assertEqual(len(m['conversations']), 15)
            self.assertEqual(len({r['conversation_id'] for r in m['conversations']}), 15)
            clean = next(r for r in m['conversations'] if r['cohort'] == 'no_issue')
            self.assertTrue(all(v == 0 for v in clean['labels'].values()))

    def test_duplicate_conversation_keeps_disputed_annotations(self):
        rows = [row('clean unique')]
        rows += [row('unique-' + label, label=label) for label in dataset.LABELS]
        rows.append(row('unique-1.1'))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'dataset.json'
            save(path, rows)
            with patch.object(dataset, 'download', return_value=path):
                m = dataset.sample(d, count=1, include_disputed=True)
            self.assertEqual(len(m['conversations']), 15)
            target = next(r for r in m['conversations'] if r['cohort'] == '1.1')
            self.assertTrue(target['disputed_annotations'])
            self.assertEqual(len(target['all_source_annotations']), 2)
            self.assertEqual({r['labels']['1.1'] for r in target['all_source_annotations']}, {0, 1})


if __name__ == '__main__':
    unittest.main()
