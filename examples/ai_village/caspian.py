"""Read-only CASPIAN input-readiness audit for this application's exported schema.

No payload text, credentials, or source rows are emitted. No causal pairs are guessed.
"""
import argparse
import json
from pathlib import Path

TABLES = ('chat_messages-selected', 'chat_rooms', 'computer_use_turns-selected', 'agent_memories-selected')


def audit_tables(tables):
    summaries = {
        name: {'rows': len(tables.get(name, [])),
               'fields': sorted({key for row in tables.get(name, []) for key in row})}
        for name in TABLES
    }
    return {
        'application': 'ai_village', 'method': 'caspian', 'status': 'insufficient_directed_evidence',
        'tables': summaries,
        'channels': {
            'comm': {'schema_can_supply': 'Message text, sender, room and timestamps',
                     'missing': 'Verified recipient exposure and downstream response pairing; versioned text encoder'},
            'mem': {'schema_can_supply': 'Agent-owned memory snapshots',
                    'missing': 'Writer-to-reader artifact lineage and observed reader behavior'},
            'tool': {'schema_can_supply': 'Local computer action and output',
                     'missing': 'Cross-agent tool-output reuse and downstream behavior pairing'},
            'exec': {'schema_can_supply': 'Turn timestamps and error field',
                     'missing': 'Verified cross-agent execution dependency and measured latency/token feature contract'},
        },
        'required_application_work': [
            'Define observable recipients from actual exposure logs; a room is not proof of a read.',
            'Match a source to a later observed target response without looking beyond the analysis cursor.',
            'Supply a fixed topology, turn boundaries, encoder identity and feature dimensions.',
            'Record memory/tool artifact reads and runtime metrics to enable their channels.',
            'Use ChannelEvent/Turn through a versioned HistoryAdapter after those inputs exist.',
        ],
        'interpretation': 'Schema readiness only. This application has no verified mapping for CASPIAN pairs; no scores or attack conclusions are produced.',
    }


def audit(root):
    tables = {}
    for name in TABLES:
        path = Path(root) / f'{name}.json'
        if path.exists():
            rows = json.loads(path.read_text())
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError(f'{name} must contain a list of records')
            tables[name] = rows
    return audit_tables(tables)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Existing AI Village source directory; read only')
    args = parser.parse_args()
    if args.source is not None and not args.source.is_dir():
        parser.error('--source must be an existing directory')
    # Empty schema audit is useful without downloading private/gated example data.
    print(json.dumps(audit(args.source) if args.source else audit_tables({}), indent=2))


if __name__ == '__main__':
    main()
