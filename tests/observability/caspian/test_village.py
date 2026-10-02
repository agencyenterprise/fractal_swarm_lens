import json

from examples.ai_village.caspian import audit, audit_tables


def test_audit_never_fabricates_pairs_or_exposes_payloads(tmp_path):
    table = [{'id': 'm1', 'content': 'PRIVATE_SENTINEL', 'agent_speaker_id': 'a', 'room_id': 'r'}]
    (tmp_path / 'chat_messages-selected.json').write_text(json.dumps(table))
    before = (tmp_path / 'chat_messages-selected.json').read_bytes()
    result = audit(tmp_path)
    assert result['status'] == 'insufficient_directed_evidence'
    assert result['tables']['chat_messages-selected']['rows'] == 1
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)
    assert 'scores' not in result
    assert (tmp_path / 'chat_messages-selected.json').read_bytes() == before


def test_audit_without_private_data():
    result = audit_tables({})
    assert result['status'] == 'insufficient_directed_evidence'
    assert all(table['rows'] == 0 for table in result['tables'].values())
