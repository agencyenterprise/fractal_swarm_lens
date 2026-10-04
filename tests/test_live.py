from dataclasses import asdict
import json
import pytest
from fastapi.testclient import TestClient

from swarm_lens.core.models import Fact, Conflict
from swarm_lens.live.service import LiveService
from swarm_lens.web.api import create_app

AT = '2026-10-03T12:00:00+00:00'


def test_capture_retry_reconnect_and_restart(framework, tmp_path):
    path = tmp_path/'live.sqlite'
    live = LiveService(framework, path)
    with TestClient(create_app(framework, live=live)) as client:
        request = {'client_id':'stable-source','name':'Live','metadata':{'framework':'crewai'}}
        branch = client.post('/api/live/runs', json=request).json()['branch_id']
        assert client.post('/api/live/runs', json=request).json()['branch_id'] == branch
        assert client.post('/api/live/runs', json={**request,'name':'different'}).status_code == 409
        facts = [asdict(Fact('environment.updated', {'goal':'first'}, AT))]
        body = {'expected_head':0,'facts':facts}
        with client.websocket_connect(f'/api/live/branches/{branch}/stream?after=0') as ws:
            assert ws.receive_json()['cursor'] == 0
            assert client.post(f'/api/live/branches/{branch}/events', json=body).status_code == 200
            result = ws.receive_json()
            assert result['cursor'] == 1
            assert len(result['events']) == 1
        assert client.post(f'/api/live/branches/{branch}/events', json=body).json()['duplicate']
        changed = json.loads(json.dumps(body))
        changed['facts'][0]['data']['goal'] = 'changed'
        assert client.post(f'/api/live/branches/{branch}/events', json=changed).status_code == 409
        facts2 = [asdict(Fact('observation.recorded', {'type':'second'}, AT))]
        client.post(f'/api/live/branches/{branch}/events', json={'expected_head':1,'facts':facts2})
        with client.websocket_connect(f'/api/live/branches/{branch}/stream?after=1') as ws:
            assert [e['position'] for e in ws.receive_json()['events']] == [2]
        with pytest.raises(Exception):
            with client.websocket_connect(f'/api/live/branches/{branch}/stream', headers={'Origin':'https://foreign.example'}):
                pass
        assert client.post(f'/api/branches/{branch}/interventions',json={'kind':'environment.updated','data':{'goal':'no'},'expected_head':2}).status_code == 409
        client.post(f'/api/live/branches/{branch}/finish',json={'status':'completed'})
    live2 = LiveService(framework,path)
    with TestClient(create_app(framework,live=live2)) as client:
        response = client.get(f'/api/live/branches/{branch}/events?after=0').json()
        assert len(response['events']) == 2
        assert response['capture']['status'] == 'completed'


def test_atomic_bad_batch(framework,tmp_path):
    live = LiveService(framework,tmp_path/'live.sqlite')
    record = live.create_capture('x','test',{})
    facts = [Fact('environment.updated',{'goal':'valid'},AT),Fact('message.created',{'id':'missing-channel'},AT)]
    with pytest.raises(ValueError):
        live.append(record['branch_id'],facts,0)
    assert framework.store.branch(record['branch_id']).head == 0
    live.close()


def test_interrupted_jobs_never_auto_execute(framework,tmp_path):
    live = LiveService(framework,tmp_path/'live.sqlite')
    job = {'id':'pending','branch_id':'branch','status':'running'}
    with live.db() as db:
        db.execute('INSERT INTO execution_jobs VALUES (?,?,?,?)',('pending','branch','running',json.dumps(job)))
    live.recover()
    assert live.job('pending')['status'] == 'interrupted'
    live.close()


def test_outbox_recovers_after_lost_ack(framework,tmp_path,monkeypatch):
    from swarm_lens.live.client import LiveClient
    live=LiveService(framework,tmp_path/'jobs.sqlite')
    with TestClient(create_app(framework,live=live)) as api:
        lost=[True]
        def send(self,path,body):
            response=api.post(path,json=body)
            response.raise_for_status()
            if path.endswith('/events') and lost[0]:
                lost[0]=False
                raise ConnectionError('ack lost')
            return response.json()
        monkeypatch.setattr(LiveClient,'_post',send)
        collector=LiveClient('http://testserver','Capture',{},spool_dir=tmp_path/'outbox')
        with pytest.warns(RuntimeWarning,match='delivery paused'):
            collector.emit([Fact('environment.updated',{'goal':'once'},AT)])
        collector.finish()
        recovered=LiveClient.recover(collector.path,'http://testserver')
        assert not recovered.offline
        assert recovered.head==1
        assert framework.store.branch(recovered.branch_id).head==1
        assert live.capture(recovered.branch_id)['status']=='completed'


def test_collector_disk_failure_is_visible_and_does_not_abort(tmp_path,monkeypatch):
    from swarm_lens.live.client import LiveClient
    from pathlib import Path
    def denied(*args,**kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(Path,'open',denied)
    with pytest.warns(RuntimeWarning,match='Capture is incomplete'):
        client=LiveClient('http://unused','test',{},spool_dir=tmp_path)
    client.emit([Fact('observation.recorded',{'type':'lost'},AT)])
    assert client.storage_error and not client.finish()


def test_launch_retry_live_events_and_capture_ownership(framework, tmp_path):
    from threading import Event
    from uuid import uuid4
    entered, release = Event(), Event()
    class App:
        id = 'test-app'
        calls = 0
        def launch_info(self):
            return dict(id=self.id, title='Test application', description='Test', ready=True,
                        framework='test', framework_version='1', revision='1')
        def start(self, emit):
            self.calls += 1
            emit([Fact('environment.updated', {'goal':'First live event'}, AT)])
            entered.set()
            assert release.wait(10)
            emit([Fact('observation.recorded', {'type':'finished'}, AT)])
    runtime = App()
    live = LiveService(framework, tmp_path/'jobs.sqlite', (runtime,))
    body = {'request_id': str(uuid4())}
    with TestClient(create_app(framework, live=live)) as client:
        assert client.get('/api/live/apps').json()[0]['id'] == runtime.id
        assert client.post('/api/live/apps/unknown/start', json=body).status_code == 400
        assert not framework.store.runs()
        response = client.post('/api/live/apps/test-app/start', json=body)
        assert response.status_code == 202
        job = response.json()
        try:
            assert entered.wait(10)
            assert client.post('/api/live/apps/test-app/start', json=body).json()['id'] == job['id']
            assert client.post('/api/live/apps/other/start', json=body).status_code == 409
            branch = job['branch_id']
            assert client.post(f'/api/live/branches/{branch}/finish', json={'status':'completed'}).status_code == 409
            assert client.post(f'/api/live/branches/{branch}/events', json={
                'expected_head':1, 'facts':[asdict(Fact('observation.recorded', {'type':'injected'}, AT))]}).status_code == 409
            with client.websocket_connect(f'/api/live/branches/{branch}/stream?after=0') as ws:
                update = ws.receive_json()
                assert update['cursor'] == 1 and update['jobs'][0]['status'] == 'running'
                assert update['capture']['status'] == 'capturing'
        finally:
            release.set()
    assert runtime.calls == 1 and len(framework.store.runs()) == 1
    assert live.job(job['id'])['status'] == 'completed'
    assert live.capture(branch)['status'] == 'completed'
    assert framework.store.branch(branch).head == 2
    # A lost acknowledgement can be retried after completion, without another provider call.
    assert live.submit_start(runtime.id, body['request_id'])['status'] == 'completed'
    assert runtime.calls == 1


def test_launch_failure_and_restart_close_managed_capture(framework, tmp_path):
    class App:
        id = 'failing'
        def launch_info(self):
            return dict(id=self.id, title='Test', ready=True, framework='test', framework_version='1', revision='1')
        def start(self, emit):
            emit([Fact('environment.updated', {'goal':'Preserved'}, AT)])
            raise RuntimeError('provider secret must not escape')
    live = LiveService(framework, tmp_path/'jobs.sqlite', (App(),))
    job = live.submit_start('failing', 'failed-request')
    live.close()
    assert live.job(job['id'])['status'] == 'failed'
    assert 'provider secret' not in json.dumps(live.job(job['id']))
    assert live.capture(job['branch_id'])['status'] == 'failed'
    assert framework.store.branch(job['branch_id']).head == 1
    capture = live.create_capture('interrupted', 'Test', {})
    interrupted = dict(id='interrupted', kind='start', branch_id=capture['branch_id'], status='running')
    with live.db() as db:
        db.execute('INSERT INTO execution_jobs VALUES (?,?,?,?)',
                   (interrupted['id'], interrupted['branch_id'], interrupted['status'], json.dumps(interrupted)))
    live.recover()
    assert live.job('interrupted')['status'] == 'interrupted'
    assert live.capture(capture['branch_id'])['status'] == 'incomplete'
