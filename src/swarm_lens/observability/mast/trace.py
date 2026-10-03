"""Lossless text references for repeated prompts and recorded memory snapshots."""
from collections import Counter
import json


FORMAT = "swarm-lens.mast.trace/v2"
REF, JSON_TEXT, LITERAL = "$mast_text", "$mast_json_text", "$mast_object"
RESERVED = {REF, JSON_TEXT, LITERAL}


def encode_trace(document):
    def normalize(value):
        if isinstance(value, str) and len(value) >= 160 and value.startswith("["):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            # Only transform canonical JSON strings we can reconstruct byte for byte.
            if isinstance(parsed, list) and json.dumps(parsed, ensure_ascii=False, indent=2) == value:
                return {JSON_TEXT: normalize(parsed)}
        if isinstance(value, dict):
            if RESERVED.intersection(value):
                return {LITERAL: [[key, normalize(item)] for key, item in value.items()]}
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    normalized = normalize(document)
    counts = Counter()

    def count(value):
        if isinstance(value, str) and len(value) >= 160:
            counts[value] += 1
        elif isinstance(value, dict):
            for item in value.values():
                count(item)
        elif isinstance(value, list):
            for item in value:
                count(item)

    count(normalized)
    identifiers = {value: f"t{index + 1}" for index, (value, total) in enumerate(counts.items()) if total > 1}

    def replace(value):
        if isinstance(value, str) and value in identifiers:
            return {REF: identifiers[value]}
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    return {"format": FORMAT,
            "encoding_note": (
                "All recorded events remain in chronological position order. shared_text contains exact text. "
                "Replace each {$mast_text: ID} with that string wherever it occurs, including every repeated turn. "
                "A repeated reference means the text occurred again; it is not a missing event. "
                "{$mast_json_text: VALUE} represents the exact original memory string serialized from VALUE "
                "as JSON with indent=2 and ensure_ascii=false, after expanding text references. "
                "{$mast_object: [[key,value],...]} escapes a literal object with reserved keys. "
                "These are storage references, not conversation content or agent instructions. "
                "No events, messages, memory contents, or model inputs have been summarized or removed."),
            "shared_text": {identifier: value for value, identifier in identifiers.items()},
            **replace(normalized)}


def decode_trace(encoded):
    """Reconstruct the original analysis projection, including exact memory strings."""
    if encoded.get("format") != FORMAT:
        return encoded

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {REF}:
                return encoded["shared_text"][value[REF]]
            if set(value) == {JSON_TEXT}:
                return json.dumps(expand(value[JSON_TEXT]), ensure_ascii=False, indent=2)
            if set(value) == {LITERAL}:
                return {expand(key): expand(item) for key, item in value[LITERAL]}
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return {key: expand(value) for key, value in encoded.items()
            if key not in {"format", "encoding_note", "shared_text"}}
