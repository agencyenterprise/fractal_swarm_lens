"""Import a preserved multi-agent message-board batch as Swarm Lens runs, one run per world."""
import argparse
import hashlib
import json
from pathlib import Path

from swarm_lens import Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory

ADAPTER = 'messageboard/v1'
BOARD = 'board'
# Text delivered privately to one agent by the simulator, not posted by another agent.
DELIVERIES = {'injection_delivered': 'Task injection', 'peer_norms_assignment_loaded': 'Assignment loaded',
              'world_event_delivered': 'World event'}


def inbox(agent_id):
    return f'inbox-{agent_id}'


def summary(row):
    """One readable line for an observation; the full original record stays in the fact data."""
    kind = row['event_type']
    if kind == 'shell_executed':
        return f"$ {row['command']}  (exit {row['exit_code']})"
    if kind == 'internal_endpoint_observed':
        return f"{row['method']} {row['request_path']} -> {row['status_code']}"
    if kind == 'api_document_read':
        return f"read {row['document_path']} (found: {row['found']})"
    if kind == 'http_board_served':
        return f"board served {len(row['seed_ids'])} archive posts, {len(row['message_ids'])} live messages"
    if kind == 'api_result_submitted':
        return 'submitted: ' + json.dumps(row['answer'], ensure_ascii=False)
    if kind == 'peer_norms_score_recorded':
        return 'outcomes: ' + json.dumps(row['outcomes'])
    if kind == 'round_result_recorded':
        return row['reason']
    return kind.replace('_', ' ')


class WorldSource:
    """Facts for one world's event log, in recorded order."""

    def __init__(self, rows, run_id, featured_ids=()):
        self.rows, self.run_id, self.featured_ids = rows, run_id, set(featured_ids)

    def fact(self, kind, data, row):
        source = {'adapter': ADAPTER, 'run': self.run_id, 'event_id': row['event_id'], 'event_type': row['event_type']}
        if row['event_id'] in self.featured_ids:
            source['featured'] = True
        return Fact(kind, data, row['timestamp'], source)

    def facts(self):
        agents = [row['agent_id'] for row in self.rows if row['event_type'] == 'agent_registered']
        for row in self.rows:
            kind, at = row['event_type'], row['timestamp']
            agent = row.get('agent_id')
            stage = {'round': row['round_number']}
            if kind == 'simulation_started':
                yield self.fact('environment.updated', {'task': row['scenario_description'],
                                                        'goal': row['scenario_description']}, row)
                yield self.fact('observation.recorded', {'type': kind, 'content': summary(row),
                                                         'record': row, 'metadata': stage}, row)
            elif kind == 'agent_registered':
                yield self.fact('agent.added', {'id': agent, 'name': row['role_name'], 'model': row['model'],
                                                'system_prompt': row['system_prompt'],
                                                'metadata': {'provider': row['provider'], 'tools': row['tool_names'],
                                                             'max_tokens': row['max_tokens']}}, row)
                yield self.fact('channel.created', {'id': inbox(agent), 'name': f"{row['role_name']} inbox",
                                                    'members': [agent]}, row)
                if agent == agents[-1]:
                    yield self.fact('channel.created', {'id': BOARD, 'name': 'Board', 'members': agents}, row)
            elif kind == 'peer_norms_api_task_opened':
                yield self.fact('environment.updated', {'task': row['problem_statement'],
                                                        'goal': 'Deliver the requested records'}, row)
                yield self.fact('observation.recorded', {'type': kind, 'content': row['problem_statement'],
                                                         'record': row, 'metadata': stage}, row)
            elif kind in DELIVERIES:
                yield self.fact('message.created', {'id': row['event_id'], 'channel_id': inbox(agent), 'role': 'system',
                                                    'sender_name': 'Simulator', 'content': row['text'],
                                                    'metadata': {**stage, 'stage_label': DELIVERIES[kind],
                                                                 'recipient': agent}}, row)
            elif kind == 'llm_response_received':
                # The agent's own turn. It is not posted anywhere others can read; posting happens via send_message.
                content = row['text'] or row['thinking'] or ''
                yield self.fact('message.created', {'id': row['event_id'], 'channel_id': inbox(agent), 'sender_id': agent,
                                                    'content': content,
                                                    'metadata': {**stage, 'content_kind': 'text' if row['text'] else 'thinking',
                                                                 'thinking': row['thinking'], 'tool_calls': row['tool_calls'],
                                                                 'stop_reason': row['stop_reason'], 'usage': row['usage']}}, row)
            elif kind == 'tool_call_invoked':
                yield self.fact('tool.started', {'id': row['call_id'], 'agent_id': agent, 'tool_name': row['tool_name'],
                                                 'arguments': row['arguments'], 'status': 'running',
                                                 'metadata': stage}, row)
            elif kind == 'tool_result_received':
                yield self.fact('tool.completed', {'id': row['call_id'], 'status': 'completed', 'result': row['result']}, row)
            else:
                yield self.fact('observation.recorded', {'type': kind, 'agent_id': agent, 'content': summary(row),
                                                         'record': row, 'metadata': stage}, row)


def world_rows(dataset, entry):
    (path,) = entry['event_files']  # One event log per world
    raw = (dataset / path).read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if len(rows) != entry['event_count']:
        raise ValueError(f"{entry['run_id']}: {len(rows)} events on disk, manifest says {entry['event_count']}")
    return rows, hashlib.sha256(raw).hexdigest()


def outcome(rows):
    scores = [row for row in rows if row['event_type'] == 'peer_norms_score_recorded']
    return scores[-1]['outcomes'] if scores else None


def import_batch(dataset, data):
    """Import every world once; re-running skips worlds already imported from the same log bytes."""
    dataset = Path(dataset)
    manifest = json.loads((dataset / 'manifest.json').read_text())
    featured = manifest.get('featured_case') or {}
    framework = Framework(SQLiteHistory(Path(data) / 'history.sqlite'))
    existing = {run.metadata.get('example_key') for run in framework.store.runs()}
    imported = []
    for entry in manifest['runs']:
        rows, digest = world_rows(dataset, entry)
        key = f"{manifest['original_batch_id']}:{entry['run_id']}:{digest}"
        if key in existing:
            continue
        is_featured = featured.get('run_id') == entry['run_id']
        label = ' · featured: ' + featured['label'] if is_featured else ''
        branch = framework.create_run(f"Messageboard · {entry['run_id']} · {entry['status']}{label}", metadata={
            'source_type': 'messageboard', 'example_key': key, 'batch_id': manifest['original_batch_id'],
            'run_id': entry['run_id'], 'model': entry['model'], 'status': entry['status'],
            'task_title': f"Health statistics import · {entry['run_id']}", 'log_sha256': digest,
            'outcomes': outcome(rows), 'featured_case': featured if is_featured else None,
            'start': entry['first_event_at']})
        framework.ingest(branch.id, WorldSource(rows, entry['run_id'], featured.get('event_ids', []) if is_featured else ()))
        state = framework.state(branch.id)
        imported.append({'run_id': entry['run_id'], 'branch_id': branch.id, 'events': state.cursor,
                         'agents': len(state.agents), 'messages': len(state.messages), 'tools': len(state.tools)})
    return imported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Dataset folder that contains manifest.json')
    parser.add_argument('--data', type=Path, required=True, help='Swarm Lens data folder')
    args = parser.parse_args()
    print(json.dumps(import_batch(args.input, args.data), indent=2))


if __name__ == '__main__':
    main()
