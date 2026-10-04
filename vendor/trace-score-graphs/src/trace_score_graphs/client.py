"""Resumable Decisions API transport with raw evidence and no synthetic fallback."""
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from .common import digest, read, save

ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
MODEL = 'typesafe/jev-1.13-20260917'


def finite(value, lo, hi):
    return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and lo <= value <= hi


def validate(response, questions):
    answers = response.get('answers', {})
    if set(answers) != set(questions) or not response.get('model'):
        raise ValueError('Missing decisions or resolved model')
    for name, q in questions.items():
        a = answers[name]
        if a.get('type') != q['type']:
            raise ValueError('Decision type mismatch')
        if q['type'] == 'score':
            levels = len(q['criteria'])
            expected = {str(i) for i in range(levels)}
            if not finite(a.get('score'), 0, levels - 1):
                raise ValueError('Score out of range')
        else:
            expected = set(q['criteria'])
            if a.get('choice') not in expected:
                raise ValueError('Unknown categorical answer')
        probabilities = a.get('probabilities', {})
        if set(probabilities) != expected or any(not finite(p, 0, 1) for p in probabilities.values()):
            raise ValueError('Invalid decision distribution')
        # Provider rounds probabilities; retain original values rather than renormalizing.
        if abs(sum(probabilities.values()) - 1) > .005 * len(expected) + .000001:
            raise ValueError('Distribution does not sum to one')
        if q['type'] == 'score':
            mean = sum(int(k) * p for k, p in probabilities.items())
            tolerance = .005 * sum(range(levels)) + .005 + .000001
            if abs(mean - a['score']) > tolerance:
                raise ValueError('Score inconsistent with distribution')
        if a.get('confidence') is not None and not finite(a['confidence'], 0, 1):
            raise ValueError('Invalid confidence')
    if not finite(response.get('usage', {}).get('cost'), 0, float('inf')):
        raise ValueError('Missing usage cost')


class Client:
    def __init__(self, cache, model=MODEL, max_requests=50000, max_cost=25.0):
        self.cache, self.model = Path(cache), model
        self.key = os.environ.get('OPENROUTER_API_KEY')
        self.max_requests, self.max_cost = max_requests, max_cost
        self.lock = threading.Lock()
        self.requests, self.hits, self.cost = 0, 0, 0.0

    def decide(self, state, questions):
        request = {'model': self.model, 'state': state, 'questions': questions}
        data = json.dumps(request, ensure_ascii=False).encode()
        if len(data) > 32000:
            raise ValueError('Request exceeds conservative context bound')
        key = digest({'endpoint': ENDPOINT, 'request': request})
        path = self.cache / (key + '.json')
        if path.exists():
            record = read(path)
            if record['request'] != request or record['endpoint'] != ENDPOINT:
                raise ValueError('Cache mismatch')
            validate(record['response'], questions)
            with self.lock:
                self.hits += 1
            return key, record
        if not self.key:
            raise ValueError('OPENROUTER_API_KEY is missing')
        for attempt in range(5):
            with self.lock:
                if self.requests >= self.max_requests or self.cost >= self.max_cost:
                    raise RuntimeError('Configured request or spend stop reached; resume from cache')
                self.requests += 1
            req = urllib.request.Request(ENDPOINT, data=data, headers={
                'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                'X-Title': 'MAST independent score graphs'})
            try:
                with urllib.request.urlopen(req, timeout=90) as stream:
                    raw = stream.read().decode()
            except urllib.error.HTTPError as error:
                # Only a rejected/rate-limited call is automatically retried.
                if error.code == 429 and attempt < 4:
                    time.sleep(min(2 ** attempt, 16))
                    continue
                raise RuntimeError(f'Decisions API HTTP {error.code}; no scores inferred') from None
            except (OSError, TimeoutError):
                raise RuntimeError('Decisions transport failed; uncertain calls are not automatically retried') from None
            try:
                response = json.loads(raw)
            except ValueError:
                save(self.cache / (key + '.invalid.json'), {'request': request, 'raw_response': raw})
                raise ValueError('Invalid JSON from Decisions API') from None
            record = {'endpoint': ENDPOINT, 'request_sha256': key, 'request': request,
                      'response': response, 'received_at_unix': time.time()}
            save(path, record)
            cost = response.get('usage', {}).get('cost')
            if finite(cost, 0, float('inf')):
                with self.lock:
                    self.cost += cost
            validate(response, questions)
            return key, record
        raise RuntimeError('Rate limit retries exhausted')
