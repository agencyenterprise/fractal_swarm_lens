"""Selective public AI Village recording, with an explicit CrewAI continuation branch.

No model calls occur during fetch/import/prepare. Computer sessions, screenshots and
private memory are not retrieved; public session notices remain observations.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from swarm_lens import Framework, Fact
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.adapters.sqlite import SQLiteHistory

BASE = 'https://theaidigest.org/village'
VILLAGE = '00ebc425-074c-466f-ab2d-5aa2efa445aa'
GOAL = 'ee7a006d-c196-4824-b804-ead8e9632228'
TITLE = 'Perform novel research!'
DATES = tuple(f'2026-05-{day:02d}' for day in range(11, 16))
ADAPTER = 'ai-village-public-research/v1'
SAMPLE = Path(__file__).parent / 'samples' / 'ai-village-novel-research-20260511'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(url, params=None):
    import requests
    # Bound each response; never fetch the multi-GB tables or screenshots.
    with requests.get(url, params=params, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        raw = bytearray()
        for part in response.iter_content(65536):
            raw.extend(part)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError('Replay response exceeds the 32 MiB per-page limit')
    return json.loads(raw), bytes(raw)


def fetch(output):
    """Fetch only the five replay dates. Pagination must finish, or no manifest is written."""
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new output directory to preserve the existing recording')
    village, raw = read_json(f'{BASE}/api/villages/{VILLAGE}')
    pages, rows, total_bytes = [], [], len(raw)
    for date in DATES:
        seen = set()
        for page in range(1, 101):
            payload, raw = read_json(f'{BASE}/api/events', {'villageId': VILLAGE, 'date': date, 'page': page})
            if payload.get('windowDate') != date:
                raise ValueError('The API returned a different replay date')
            batch = payload['events']
            if not batch or not {e['id'] for e in batch} - seen:
                raise ValueError('Empty or repeating replay page; extraction is incomplete')
            seen.update(e['id'] for e in batch)
            rows.extend(batch)
            pages.append({'date': date, 'page': page, 'events': len(batch), 'sha256': digest(raw),
                          'bytes': len(raw), 'has_more': payload['hasMore']})
            total_bytes += len(raw)
            if not payload['hasMore']:
                break
        else:
            raise ValueError('Replay pagination exceeded 100 pages')
    return save_sample(output, village, rows, pages, total_bytes)


def save_sample(output, village, rows, pages, total_bytes):
    """Package public records; retain full event payloads but only relevant roster fields."""
    by_id = {}
    for row in rows:
        if row['id'] in by_id and row != by_id[row['id']]:
            raise ValueError('Conflicting duplicate source event')
        by_id[row['id']] = row
    rows = sorted(by_id.values(), key=lambda e: e['eventIndex'])
    if not rows or len({e['eventIndex'] for e in rows}) != len(rows):
        raise ValueError('Missing events or duplicate event indices')
    if any(e['villageId'] != VILLAGE or e['createdAt'][:10] not in DATES for e in rows):
        raise ValueError('Event outside the selected village/dates')
    if any(a['createdAt'] > b['createdAt'] for a, b in zip(rows, rows[1:])):
        raise ValueError('Source timestamps contradict event order')
    goal = next(g for g in village['villageGoals'] if g['id'] == GOAL)
    if goal['goal'].lower() != TITLE.lower():
        raise ValueError('Unexpected goal title')
    actor_ids = {actor(e['data']) for e in rows} - {None}
    room_ids = {e['data'].get('roomId') for e in rows} - {None}
    agents = [{k: a[k] for k in ('id', 'name', 'modelString')} for a in village['agents'] if a['id'] in actor_ids]
    rooms = [{k: r[k] for k in ('id', 'name')} for r in village['chatRooms'] if r['id'] in room_ids]
    if actor_ids != {a['id'] for a in agents} or room_ids != {r['id'] for r in rooms}:
        raise ValueError('Missing agent or room in the roster')
    raw = ''.join(json.dumps(e, ensure_ascii=False, separators=(',', ':')) + '\n' for e in rows).encode()
    compressed = gzip.compress(raw, mtime=0)
    manifest = {'adapter': ADAPTER, 'title': TITLE, 'village_id': VILLAGE, 'goal': goal,
                'source_url': f'{BASE}/goal/perform-novel-research', 'dates': list(DATES),
                'attribution': 'AI Digest / AI Village', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'selection': 'All public replay events returned for the five sessions, May 11–15; not the full underlying dataset.',
                'limitations': ['Computer-use turns, screenshots, private memories and original prompts were not fetched.',
                               'Room presence is updated only when observed in an event; it is not proof of message delivery.',
                               'Roster model names describe the export; original per-event outputs are preserved.',
                               'CrewAI continuation uses reconstructed chat context, not the original execution environment.'],
                'agents': agents, 'rooms': rooms, 'pages': pages, 'download_bytes': total_bytes,
                'event_count': len(rows), 'action_counts': dict(Counter(e['data']['actionType'] for e in rows)),
                'events_sha256': digest(compressed)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'events.jsonl.gz').write_bytes(compressed)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def actor(data):
    if data['actionType'] == 'USER_TALK':
        return None
    return data.get('agentId') or data.get('speakerId')


def load_sample(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    raw = (root / 'events.jsonl.gz').read_bytes()
    if manifest['adapter'] != ADAPTER or digest(raw) != manifest['events_sha256']:
        raise ValueError('Sample version or checksum mismatch')
    rows = [json.loads(line) for line in gzip.decompress(raw).splitlines()]
    if len(rows) != manifest['event_count'] or len({e['id'] for e in rows}) != len(rows):
        raise ValueError('Sample is incomplete or contains duplicate events')
    if [e['eventIndex'] for e in rows] != sorted({e['eventIndex'] for e in rows}):
        raise ValueError('Source event order is invalid')
    return manifest, rows


class ResearchSource:
    def __init__(self, manifest, rows, artifacts):
        self.manifest, self.rows, self.artifacts = manifest, rows, artifacts

    def fact(self, kind, data, row, suffix=''):
        identity = str(uuid5(NAMESPACE_URL, f'{ADAPTER}:{row["id"]}:{kind}:{suffix}'))
        return Fact(kind, data, row['createdAt'], {'adapter': ADAPTER, 'origin': 'dataset',
                    'event_id': row['id'], 'event_index': row['eventIndex']}, identity)

    def facts(self):
        first = self.rows[0]
        yield self.fact('environment.updated', {'task': TITLE, 'goal': TITLE,
                        'metadata': {'source_url': self.manifest['source_url'],
                                     'limitations': self.manifest['limitations']}}, first)
        agents = {a['id']: a for a in self.manifest['agents']}
        rooms = {r['id']: r for r in self.manifest['rooms']}
        known_agents, known_rooms, locations = set(), set(), {}
        for row in self.rows:
            data = row['data']; aid = actor(data); room = data.get('roomId')
            if aid and aid not in known_agents:
                a = agents[aid]
                yield self.fact('agent.added', {'id': aid, 'name': a['name'], 'model': a['modelString'],
                                'metadata': {'model_provenance': 'roster at retrieval', 'system_prompt_available': False}}, row)
                known_agents.add(aid)
            if room and room not in known_rooms:
                yield self.fact('channel.created', {'id': room, 'name': rooms[room]['name'],
                                'metadata': {'membership': 'observed presence; not verified recipient exposure'}}, row)
                known_rooms.add(room)
            if aid and room and locations.get(aid) != room:
                old = locations.get(aid); locations[aid] = room
                for changed in [r for r in (old, room) if r]:
                    yield self.fact('channel.updated', {'id': changed,
                                    'members': sorted(a for a, r in locations.items() if r == changed)}, row, changed)
            action = data['actionType']
            artifact = self.artifacts.put(json.dumps(row, ensure_ascii=False).encode())
            metadata = {'source_event_index': row['eventIndex'], 'stage_label': row['createdAt'][:10],
                        'original_event_artifact': artifact}
            if 'inputTokens' in data or 'outputTokens' in data:
                metadata['usage'] = {'input_tokens': data.get('inputTokens'), 'output_tokens': data.get('outputTokens')}
            if action in ('AGENT_TALK', 'USER_TALK'):
                if not room or not isinstance(data.get('content'), str):
                    raise ValueError('Chat event lacks room/content')
                yield self.fact('message.created', {'id': data.get('messageId') or row['id'], 'channel_id': room,
                                'sender_id': aid, 'sender_name': agents[aid]['name'] if aid else data.get('speakerName'),
                                'role': 'agent' if aid else 'human', 'content': data['content'],
                                'metadata': metadata}, row)
            else:
                # A session plan or a quoted tool action is not an observed computer execution.
                yield self.fact('observation.recorded', {'type': action, 'agent_id': aid,
                                'content': data.get('nextSessionGoal') or data.get('query') or data.get('roomName') or action,
                                'metadata': {**metadata, 'room_id': room, 'session_id': data.get('computerUseSessionId')}}, row)


def import_sample(root, data):
    manifest, rows = load_sample(root)
    framework = Framework(SQLiteHistory(Path(data) / 'history.sqlite'))
    artifacts = FileArtifacts(Path(data) / 'artifacts')
    source = ResearchSource(manifest, rows, artifacts)
    # Validate the entire source before creating a run. Stable fact IDs allow interrupted imports to resume.
    facts = list(source.facts())
    from swarm_lens.core.models import State, Event
    from swarm_lens.core.reducer import apply
    state = State('validate')
    for i, fact in enumerate(facts, 1):
        apply(state, Event(fact.id, state.branch_id, i, fact.kind, fact.data, fact.occurred_at, fact.occurred_at, fact.source))
    existing = next((r for r in framework.store.runs() if r.metadata.get('adapter') == ADAPTER
                     and r.metadata.get('source_sha256') == manifest['events_sha256']), None)
    branch = (next(b for b in framework.store.branches(existing.id) if b.parent_id is None) if existing else
              framework.create_run('AI Village · Perform novel research! · May 11–15',
                  {'adapter': ADAPTER, 'source_sha256': manifest['events_sha256'],
                   'source_url': manifest['source_url'], 'attribution': manifest['attribution'],
                   'example': 'examples.crewai.ai_village_research', 'limitations': manifest['limitations']}))
    from swarm_lens.application.framework import IterableSource
    prior = framework.history(branch.id)
    if len(prior) > len(facts) or any(e.id != f.id or e.kind != f.kind for e, f in zip(prior, facts)):
        raise ValueError('Existing import does not match the saved recording')
    framework.ingest(branch.id, IterableSource(facts[len(prior):]), expected_head=len(prior))
    return framework.store.branch(branch.id)


def prepare_branch(data, branch_id, model='gpt-5.5', cursor=None):
    """Explicit model substitution on a child; never rewrite original agent/model records."""
    framework = Framework(SQLiteHistory(Path(data) / 'history.sqlite'))
    parent = framework.store.branch(branch_id)
    if framework.store.run(parent.run_id).metadata.get('adapter') != ADAPTER:
        raise ValueError('Select the AI Village research recording')
    cursor = parent.head if cursor is None else cursor
    state = framework.state(parent.id, cursor)
    if not state.agents:
        raise ValueError('Select a point after agents appear')
    child = framework.fork(parent.id, cursor, f'CrewAI continuation · {model}')
    for agent in state.agents.values():
        framework.intervene(child.id, 'agent.updated', {'id': agent.id, 'model': model},
                            framework.store.branch(child.id).head, actor='CrewAI example: explicit model substitution')
    return framework.store.branch(child.id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    download = commands.add_parser('fetch'); download.add_argument('--output', type=Path, required=True)
    ingest = commands.add_parser('import'); ingest.add_argument('--source', type=Path, default=SAMPLE)
    ingest.add_argument('--data', type=Path, default=Path('data'))
    prepare = commands.add_parser('prepare'); prepare.add_argument('--data', type=Path, default=Path('data'))
    prepare.add_argument('--branch', required=True); prepare.add_argument('--cursor', type=int)
    prepare.add_argument('--model', default='gpt-5.5')
    args = parser.parse_args()
    if args.command == 'fetch':
        result = fetch(args.output)
    elif args.command == 'import':
        result = asdict(import_sample(args.source, args.data))
    else:
        result = asdict(prepare_branch(args.data, args.branch, args.model, args.cursor))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
