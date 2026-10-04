"""Uniform evidence windows. Unknown delivery never becomes observed delivery."""
import json
from .common import digest
from .rubrics import manifest, questions

POLICY = {'version': 'evidence-v2', 'history_events': 4, 'response_turns': 1,
          'maximum_future_events': 12, 'truncation': 'utf8_head_tail_with_coverage',
          'budget_unit': 'JSON-escaped UTF-8 bytes',
          'task_bytes': 1600, 'context_event_bytes': 500,
          'message_bytes': 14000, 'source_bytes': 5000, 'response_bytes': 9000}


def clip(text, limit):
    data = text.encode()
    if len(json.dumps(text, ensure_ascii=False).encode()) <= limit:
        return {'text': text, 'truncated': False, 'original_bytes': len(data),
                'retained_bytes': len(data), 'sha256': digest(text)}
    marker = '\n[... OMITTED MIDDLE; DO NOT INFER ITS CONTENT ...]\n'
    left, right = 0, limit // 2
    while left < right:
        mid = (left + right + 1) // 2
        candidate = data[:mid].decode(errors='ignore') + marker + data[-mid:].decode(errors='ignore')
        if len(json.dumps(candidate, ensure_ascii=False).encode()) <= limit:
            left = mid
        else:
            right = mid - 1
    head = data[:left].decode(errors='ignore')
    tail = data[-left:].decode(errors='ignore') if left else ''
    return {'text': head + marker + tail,
            'truncated': True, 'original_bytes': len(data),
            'retained_bytes': len((head + tail).encode()), 'sha256': digest(text)}


def records(conversation):
    events = conversation['events']
    aliases = {a: f'Actor-{i + 1}' for i, a in enumerate(conversation['actors'])}
    instrument = digest({'rubric': manifest()['sha256'], 'policy': POLICY,
                         'schema': conversation['schema_version']})
    output = []

    def packed(event, budget):
        return {'id': event['id'], 'position': event['position'], 'actor': aliases[event['actor']],
                'kind': event['kind'], 'content': clip(event['content'], budget)}

    def emit(group, source, response=None, basis=None):
        index = source['position']
        state = {'task': clip(conversation['task'], POLICY['task_bytes']),
                 'trace_evidence_flags': conversation['coverage']['flags'],
                 'history': [packed(e, POLICY['context_event_bytes'])
                             for e in events[max(0, index - POLICY['history_events']):index]],
                 'source': packed(source, POLICY['message_bytes'] if group == 'message' else POLICY['source_bytes']),
                 'scope': group, 'delivery_basis': basis,
                 'instructions': 'Assess this evidence only. Unrecorded delivery is not confirmed exposure.'}
        if response:
            state['response'] = packed(response, POLICY['response_bytes'])
            between = events[index + 1:response['position']]
            state['intervening_events'] = [packed(e, 250) for e in between[-4:]]
            state['omitted_intervening_events'] = max(0, len(between) - 4)
        record = {'conversation_id': conversation['conversation_id'], 'scope': group,
                  'source_event': source['id'], 'response_event': response['id'] if response else None,
                  'source_position': index, 'available_at': response['position'] if response else index,
                  'source_actor': source['actor'], 'response_actor': response['actor'] if response else None,
                  'delivery_basis': basis, 'instrument_sha256': instrument, 'state': state}
        record['record_id'] = digest(record)
        # Conservative UTF-8 cap includes the complete rubric; never truncate invisibly.
        if len(json.dumps({'state': state, 'questions': questions(group)}, ensure_ascii=False).encode()) > 31000:
            raise ValueError('Evidence packet exceeds conservative Jev input cap')
        output.append(record)

    for i, source in enumerate(events):
        emit('message', source)
        following = events[i + 1:i + 1 + POLICY['maximum_future_events']]
        if source['recipients']:
            # A distinct response for each recorded recipient; skip setup prompts.
            for recipient in source['recipients']:
                response = next((e for e in following if e['actor'] == recipient
                                 and e['kind'] not in ('prompt', 'input', 'status', 'artifact_update')), None)
                if response:
                    emit('interaction', source, response, source.get('delivery_basis', 'explicit_recipient'))
        else:
            response = next((e for e in following if e['actor'] != source['actor']
                             and e['kind'] not in ('prompt', 'input', 'status', 'artifact_update')), None)
            if response:
                emit('interaction', source, response, 'temporal_candidate_unconfirmed_exposure')
    return output
