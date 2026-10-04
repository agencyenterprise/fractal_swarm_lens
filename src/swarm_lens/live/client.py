"""Small HTTP collector with a durable JSONL outbox and idempotent recovery."""
from dataclasses import asdict
import json
import os
from pathlib import Path
from threading import RLock
from urllib.request import Request, urlopen
import warnings

from swarm_lens.core.models import new_id


class LiveClient:
    def __init__(self, url, name, metadata, *, spool_dir='data/capture-outbox'):
        self.url = url.rstrip('/')
        self.id = new_id()
        self.path = Path(spool_dir) / f'{self.id}.jsonl'
        self.lock = RLock()
        self.branch_id, self.head, self.ack, self.offline = None, 0, 0, False
        self.offset, self.storage_error = 0, False
        self._record({'op': 'create', 'body': {'client_id': self.id, 'name': name, 'metadata': metadata}})

    def _record(self, record):
        # Persist before transport. No acknowledged event is lost on connection failure.
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as file:
                file.write(encoded+'\n')
                file.flush()
                os.fsync(file.fileno())
        except OSError:
            if not self.storage_error:
                warnings.warn('SwarmLens outbox could not be written. Capture is incomplete; agent execution continues.', RuntimeWarning)
            self.storage_error = True
            self.offline = True

    def _post(self, path, body):
        request = Request(self.url+path, json.dumps(body).encode(), {'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def flush(self):
        with self.lock:
            if self.storage_error:
                return False
            try:
                with self.path.open(encoding='utf-8') as file:
                    file.seek(self.offset)
                    while line := file.readline():
                        record = json.loads(line)
                        if record['op'] == 'create':
                            self.branch_id = self._post('/api/live/runs', record['body'])['branch_id']
                        elif record['op'] == 'facts':
                            response = self._post(f'/api/live/branches/{self.branch_id}/events',
                                                  {'expected_head': self.head, 'facts': record['facts']})
                            self.head = response['head']
                        else:
                            self._post(f'/api/live/branches/{self.branch_id}/finish', {'status': record['status']})
                        self.ack += 1
                        self.offset = file.tell()
                self.offline = False
                return True
            except Exception as exc:
                if not self.offline:
                    warnings.warn(f'SwarmLens delivery paused ({type(exc).__name__}). Recover from {self.path}. Agent execution continues.', RuntimeWarning)
                self.offline = True
                return False

    def emit(self, facts):
        with self.lock:
            self._record({'op': 'facts', 'facts': [asdict(fact) for fact in facts]})
            if not self.offline:
                self.flush()

    def finish(self, status='completed'):
        with self.lock:
            if self.storage_error:
                if self.branch_id:
                    try:
                        self._post(f'/api/live/branches/{self.branch_id}/finish', {'status': 'incomplete'})
                    except Exception:
                        pass  # The storage warning already identifies the lost evidence.
                return False
            self._record({'op': 'finish', 'status': status})
            return self.flush()

    @classmethod
    def recover(cls, path, url):
        client = cls.__new__(cls)
        client.url, client.path = url.rstrip('/'), Path(path)
        client.lock = RLock()
        client.branch_id, client.head, client.ack, client.offline = None, 0, 0, False
        client.offset, client.storage_error = 0, False
        with client.path.open(encoding='utf-8') as file:
            first = json.loads(file.readline())
            if first['op'] != 'create':
                raise ValueError('Not a SwarmLens capture outbox')
            last = first
            for line in file:
                last = json.loads(line)
        client.id = first['body']['client_id']
        if last['op'] != 'finish':
            client._record({'op': 'finish', 'status': 'incomplete'})
        client.flush()
        return client
