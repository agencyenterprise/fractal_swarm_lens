"""Deterministic, source-grounded adapters. Never infer failure labels."""
import ast
import bisect
import re
import textwrap
from .common import digest, text_sha

VERSION = 'events-v1'


class Builder:
    def __init__(self, text):
        self.text, self.events, self.task = text, [], ''
        self.lines = [m.start() for m in re.finditer('\n', text)]

    def add(self, start, end, actor, content=None, recipients=None, kind='message', **meta):
        content = textwrap.dedent(self.text[start:end] if content is None else content).strip()
        if not content:
            return
        self.events.append({'actor': actor or 'unknown', 'kind': kind,
            'recipients': recipients or [], 'content': content,
            'source_span': {'start': start, 'end': end,
                'line_start': bisect.bisect_left(self.lines, start) + 1,
                'line_end': bisect.bisect_left(self.lines, end) + 1}, **meta})


def blocks(pattern, text):
    matches = list(re.finditer(pattern, text, re.M))
    for i, m in enumerate(matches):
        yield m, matches[i + 1].start() if i + 1 < len(matches) else len(text)


def chatdev(b):
    task = re.search(r'^\*\*task_prompt\*\*:\s*(.+)', b.text, re.M)
    b.task = task.group(1) if task else ''
    pair = []
    for m, end in blocks(r'^\[([^\n]+? INFO)\] ([^\n]*)', b.text):
        head, body = m.group(2), b.text[m.end():end].strip()
        if head.startswith('System:') and '[chatting]' in head:
            pair = re.findall(r'\| \*\*(?:assistant|user)_role_name\*\* \| ([^|]+) \|', body)
            pair = [p.strip() for p in pair]
        turn = re.match(r'(.+?): \*\*(.+?)<->(.+?) on : (.+?), turn (\d+)\*\*', head)
        initial = re.match(r'(.+?): \*\*\[Start Chat\]\*\*', head)
        if turn or initial:
            actor = (turn or initial).group(1)
            peers = [turn.group(2), turn.group(3)] if turn else pair
            recipients = [p for p in peers if p != actor]
            if body.startswith('[ChatDev') and ']\n\n' in body:
                body = body.split(']\n\n', 1)[1]
            b.add(m.start(), end, actor, body, recipients,
                  kind='prompt' if initial else 'message',
                  delivery_basis='phase_roles', phase=turn.group(4) if turn else None,
                  timestamp=m.group(1).removesuffix(' INFO'))
        elif '[Update Codes]' in head:
            b.add(m.start(), end, 'artifact_store', body, kind='artifact_update')
        elif '[Test Reports]' in head:
            b.add(m.start(), end, 'test_runtime', body, kind='tool_result')


def literal_dicts(text):
    """Read concatenated Python repr dictionaries without executing trace code."""
    cursor = 0
    while match := re.search(r"\{'content'\s*:", text[cursor:]):
        start = cursor + match.start()
        depth, quote, escaped = 0, None, False
        for pos in range(start, len(text)):
            c = text[pos]
            if quote:
                if escaped:
                    escaped = False
                elif c == '\\':
                    escaped = True
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        value = ast.literal_eval(text[start:pos + 1])
                    except (ValueError, SyntaxError, RecursionError):
                        value = None
                    if isinstance(value, dict):
                        yield start, pos + 1, value
                    cursor = pos + 1
                    break
        else:
            break


def ag2(b):
    values = list(literal_dicts(b.text))
    if values:
        for start, end, value in values:
            content = value.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(str(x) for x in content)
            b.add(start, end, value.get('name') or value.get('role'), str(content),
                  delivery_basis='recipient_unrecorded', role=value.get('role'))
    else:
        offset = b.text.find('trajectory:')
        pattern = r'^ {6}content:(.*)\n([\s\S]*?)^ {6}role: ([^\n]+)\n {6}name: ([^\n]+)'
        for m in re.finditer(pattern, b.text[max(0, offset):], re.M):
            start, end = max(0, offset) + m.start(), max(0, offset) + m.end()
            content = m.group(1).strip() + '\n' + textwrap.dedent(m.group(2))
            b.add(start, end, m.group(4).strip(), content, delivery_basis='recipient_unrecorded',
                  role=m.group(3).strip())
        task = re.search(r'^problem_statement:\s*(.*?)(?=^other_data:|^trajectory:)', b.text, re.M | re.S)
        if task:
            b.task = textwrap.dedent(task.group(1)).strip()
    if b.events and not b.task:
        b.task = re.split(r'Problem:\s*\n?', b.events[0]['content'])[-1].strip()
    actors = sorted({e['actor'] for e in b.events})
    if len(actors) == 2:
        for e in b.events:
            e['recipients'] = [a for a in actors if a != e['actor']]
            e['delivery_basis'] = 'inferred_two_party_transcript'


def metagpt(b):
    for m, end in blocks(r'^\[([^\n]+?)\] (FROM: .*|NEW MESSAGES:)', b.text):
        header = m.group(2)
        body = b.text[m.end():end].strip().removesuffix('-' * 80).strip()
        direct = re.match(r'FROM: (.*?) TO: (.*)', header)
        if direct:
            actor = direct.group(1)
            content = body.split('CONTENT:', 1)[-1].strip().rstrip('-').strip()
            dest = re.findall(r"['\"]([^'\"]+)['\"]", direct.group(2))
            b.add(m.start(), end, actor, content, [d for d in dest if d != '<all>'],
                  delivery_basis='broadcast' if '<all>' in dest else 'explicit_header')
            if actor == 'Human' and not b.task:
                b.task = content
        else:
            # Known top-level protocol speakers only; arbitrary code labels are not actors.
            actors = list(re.finditer(r'^(SimpleCoder|SimpleTester|SimpleReviewer|Assistant|Human):[ \t]*', body, re.M))
            for i, a in enumerate(actors):
                stop = actors[i + 1].start() if i + 1 < len(actors) else len(body)
                b.add(m.start(), end, a.group(1), body[a.end():stop].strip().rstrip('-').strip(),
                      delivery_basis='recipient_unrecorded', timestamp=m.group(1))


def magentic(b):
    for m, end in blocks(r'^-{5,} ([^\n]+?) -{5,}[ \t]*$', b.text):
        body = b.text[m.end():end]
        body = re.split(r'^SCENARIO\.PY COMPLETE', body, flags=re.M)[0]
        b.add(m.start(), end, m.group(1), body, delivery_basis='recipient_unrecorded')
        if m.group(1) == 'user' and not b.task:
            b.task = body.strip()


def hyperagent(b):
    task = re.search(r'^problem_statement:\s*([\s\S]*?)(?=^other_data:|^trajectory:)', b.text, re.M)
    b.task = textwrap.dedent(task.group(1)).strip() if task else ''
    offset = max(0, b.text.find('trajectory:'))
    for m, end in blocks(r'^\s*HyperAgent_[^\n]+? - (?:INFO|ERROR) - ([^\n]*)', b.text):
        if m.start() < offset:
            continue
        header = m.group(1)
        response = re.match(r"(.+?)'s Response: ?(.*)", header)
        direct = re.match(r'([^:]+?)->([^:]+): ?(.*)', header)
        if response or direct:
            actor = (response or direct).group(1)
            recipients = [direct.group(2)] if direct else []
            content = (direct.group(3) if direct else response.group(2)) + b.text[m.end():end]
            b.add(m.start(), end, actor, content, recipients,
                  delivery_basis='explicit_header' if direct else 'recipient_unrecorded')


def appworld(b):
    task = re.search(r'^\*+ Task [^\n]+\n([^\n]+)', b.text, re.M)
    b.task = task.group(1) if task else ''
    pattern = r'^[ \t]*(Response from (.+?) Agent|Message to (.+?) Agent|Code Execution Output|Response from send_message API|Entering .+? Agent message loop|Exiting .+? Agent message loop)[ \t]*$'
    for m, end in blocks(pattern, b.text):
        header = m.group(1)
        if header.startswith(('Entering', 'Exiting')):
            continue
        actor = m.group(2) or 'environment'
        recipient = m.group(3)
        kind = 'message' if m.group(2) else 'input' if recipient else 'tool_result'
        b.add(m.start(), end, actor, b.text[m.end():end], [recipient] if recipient else [],
              kind=kind, delivery_basis='explicit_recipient' if recipient else 'recipient_unrecorded')


def openmanus(b):
    task = re.search(r'Read prompt from task.txt: ([^\n]+)', b.text)
    b.task = task.group(1) if task else ''
    pattern = r'^\d{4}-\d{2}-\d{2} [^\n]+? \| (?:INFO|WARNING|ERROR)\s*\| ([^\n]+?) - ([^\n]*)'
    pending_tools = []
    for m, end in blocks(pattern, b.text):
        origin, head = m.group(1), m.group(2)
        body = head + b.text[m.end():end]
        if 'Tools being prepared:' in head:
            pending_tools = re.findall(r"['\"]([^'\"]+)['\"]", head)
        elif "Manus's thoughts:" in head:
            b.add(m.start(), end, 'Manus', body.split("thoughts:", 1)[-1], kind='reasoning')
        elif 'Tool arguments:' in head:
            b.add(m.start(), end, 'Manus', body.split('Tool arguments:', 1)[-1],
                  ['tool:' + name for name in pending_tools],
                  kind='tool_call', delivery_basis='logged_tool_selection')
        elif 'completed its mission! Result:' in head:
            name = re.search(r"Tool ['\"](.+?)['\"]", head)
            b.add(m.start(), end, 'tool:' + (name.group(1) if name else 'unknown'),
                  body.split('Result:', 1)[-1], ['Manus'], kind='tool_result', delivery_basis='tool_result')
        elif 'Plan creation result:' in head:
            b.add(m.start(), end, 'Planner', body, ['Manus'], kind='plan', delivery_basis='workflow_plan')
        elif origin.startswith('__main__') and ('result' in head.lower() or 'terminated' in head.lower()):
            b.add(m.start(), end, 'runtime', body, kind='status')


ADAPTERS = {'ChatDev': chatdev, 'AG2': ag2, 'MetaGPT': metagpt, 'Magentic': magentic,
            'HyperAgent': hyperagent, 'AppWorld': appworld, 'OpenManus': openmanus}


def normalize(row, cid):
    raw = row['trace']['trajectory']
    b = Builder(raw)
    adapter = row['mas_name']
    if '**ChatDev Starts**' in raw[:1000]:
        adapter = 'ChatDev'
    ADAPTERS[adapter](b)
    if not b.events:
        raise ValueError(f'No events parsed for {cid}; do not score an invented conversation')
    for i, event in enumerate(b.events):
        event.update({'id': f'e{i:05d}', 'position': i})
    actors = sorted({e['actor'] for e in b.events} | {r for e in b.events for r in e['recipients']})
    flags = []
    if adapter != row['mas_name']:
        flags.append('framework_metadata_disagrees_with_log_format')
    if not b.task or b.task.endswith('...'):
        flags.append('task_missing_or_abbreviated')
    if '<image>' in raw:
        flags.append('image_content_unavailable')
    if any(not e['recipients'] for e in b.events):
        flags.append('some_recipients_unrecorded')
    return {'schema_version': VERSION, 'conversation_id': cid, 'raw_sha256': text_sha(raw),
            'adapter': adapter, 'source_framework': row['mas_name'], 'task': b.task, 'actors': actors, 'events': b.events,
            'coverage': {'events': len(b.events), 'raw_characters': len(raw), 'flags': flags,
                'source_order_preserved': True, 'scope': 'recognized logged events; text only'},
            'provenance': {'source_key': row['trace']['key'], 'source_index': row['trace']['index']}}
