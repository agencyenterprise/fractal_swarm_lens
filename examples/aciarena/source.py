"""Past-only communication features and optional Swarm Lens history persistence."""
from dataclasses import asdict
from datetime import datetime, timezone
import time

from swarm_lens import Fact
from swarm_lens.observability.caspian import Caspian, ChannelEvent, Turn

AGENTS = ('debater_0', 'debater_1', 'debater_2', 'aggregator')
EDGES = tuple((a, b) for a in AGENTS[:3] for b in AGENTS if a != b)


class FeatureBuilder:
    def __init__(self, encoder):
        self.encoder, self.cache = encoder, {}

    def build(self, index, calls):
        calls = list(calls)
        for call in calls:
            if any(source['sequence'] >= call['sequence'] for source in call['sources']):
                raise ValueError('A source response must precede its receiving agent response')
        texts = []
        for call in calls:
            if call['sources']:
                texts.append(call['response'])
                texts.extend(source['response'] for source in call['sources'])
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        vectors = self.encoder.encode(missing)
        self.cache.update(zip(missing, vectors, strict=True))
        events = []
        for call in calls:
            for source in call['sources']:
                events.append(ChannelEvent(source['agent'], call['agent'], 'comm',
                                           self.cache[source['response']], self.cache[call['response']]))
        return Turn(index, tuple(events))


class Monitor:
    def __init__(self, encoder):
        self.features = FeatureBuilder(encoder)
        self.method = Caspian(AGENTS, EDGES, feature_schema=encoder.feature_schema, observed_channels=('comm',))
        self.results, self.observations = [], []
        self.seconds = 0.0

    def update(self, index, calls):
        # Measure every turn, including aggregation, while retaining the first alert.
        turn = self.features.build(index, calls)
        self.observations.append(asdict(turn))
        started = time.perf_counter()
        self.results.append(self.method.update(turn))
        self.seconds += time.perf_counter() - started
        return self.results[-1]

    def report(self):
        return {'method': self.method.describe(), 'turns': self.results,
                'first_alert': self.method.detector.alert, 'detector_seconds': self.seconds}


class DebateSource:
    def __init__(self, trace, observations):
        self.trace, self.observations = trace, observations

    def facts(self):
        at = datetime.now(timezone.utc).isoformat()
        for agent in AGENTS:
            yield Fact('agent.added', {'id': agent, 'name': agent}, at)
        yield Fact('channel.created', {'id': 'debate', 'name': 'Observed debate', 'members': list(AGENTS)}, at)
        observations = {turn['index']: turn for turn in self.observations}
        for call in self.trace.calls:
            yield Fact('message.created', {'id': f"call-{call['sequence']}", 'channel_id': 'debate',
                                          'sender_id': call['agent'], 'content': call['response'],
                                          'metadata': {'round': call['round'], 'phase': call['phase'],
                                                       'sources': [s['sequence'] for s in call['sources']],
                                                       'usage': call['usage'], 'latency_seconds': call['latency_seconds']}}, at)
            is_last = call['sequence'] == len(self.trace.calls) or self.trace.calls[call['sequence']]['round'] != call['round']
            if is_last and call['round'] + 1 in observations:
                yield Fact('observation.recorded', {'type': 'aciarena_comm_turn',
                                                   **observations[call['round'] + 1]}, at)


class DebateHistory:
    id, version = 'aciarena-llm-debate-comm', '1'

    def describe(self):
        return {'channels': ['comm'], 'turn': 'bootstrap, then one sequential debate round, then aggregation',
                'pairing': 'actual preceding source responses delivered to the target; labels excluded'}

    def observations(self, history):
        for event in history:
            if event.kind == 'observation.recorded' and event.data.get('type') == 'aciarena_comm_turn':
                yield Turn(event.data['index'], tuple(ChannelEvent(**value) for value in event.data['events']))
