from contextlib import contextmanager
from functools import wraps

from swarm_lens.live.client import LiveClient
from .adapter import CREWAI_VERSION, ADAPTER_VERSION, CrewObserver


@contextmanager
def observe(crew, *, url='http://127.0.0.1:8766', name=None, inputs=None, runtime=None,
            spool_dir='data/capture-outbox'):
    """Wrap an existing synchronous kickoff; model and tool behavior is unchanged."""
    client = LiveClient(url, name or crew.name or 'Live CrewAI run',
                        {'framework': 'crewai', 'framework_version': CREWAI_VERSION,
                         'adapter_version': ADAPTER_VERSION, 'runtime_id': runtime.id if runtime else None},
                        spool_dir=spool_dir)
    observer = CrewObserver(crew, client.emit, inputs=inputs,
                            runtime_id=runtime.id if runtime else None, revision=runtime.revision if runtime else None)
    try:
        with observer:
            yield client
    except BaseException:
        client.finish('failed')
        raise
    else:
        client.finish('incomplete' if observer.errors else 'completed')


def traced_crew(*, url='http://127.0.0.1:8766', runtime=None, spool_dir='data/capture-outbox'):
    """Decorate a factory(inputs) -> Crew; calling it captures a normal kickoff."""
    def decorate(factory):
        @wraps(factory)
        def run(inputs=None):
            crew = factory(inputs or {})
            with observe(crew, url=url, runtime=runtime, inputs=inputs, spool_dir=spool_dir):
                return crew.kickoff(inputs=inputs)
        return run
    return decorate
