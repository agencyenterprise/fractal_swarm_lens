"""Live API smoke example using two illustrative texts, not a benchmark trace."""
import json

from swarm_lens.adapters.openai_embeddings import OpenAITextEncoder
from swarm_lens.observability.caspian import Caspian, ChannelEvent, Turn


def main():
    encoder = OpenAITextEncoder.from_env()
    source, target = encoder.encode([
        "Please verify the calculation before sharing it.",
        "I checked the calculation and will share the verified result.",
    ])
    monitor = Caspian(('sender', 'receiver'), [('sender', 'receiver')],
                      feature_schema=encoder.feature_schema, observed_channels=('comm',))
    result = monitor.update(Turn(1, (ChannelEvent('sender', 'receiver', 'comm', source, target),)))
    print(json.dumps({'encoder': encoder.describe(), 'turn': result['turn'],
                      'ready_triplets': result['evidence']['ready_triplets'],
                      'interpretation': 'API wiring only; one illustrative pair cannot establish influence.'}, indent=2))


if __name__ == '__main__':
    main()
