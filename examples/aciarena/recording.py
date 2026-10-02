from copy import deepcopy
import json
from pathlib import Path
import time
from types import SimpleNamespace


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Bound completed and in-flight token usage; no automatic API retries."""

    def __init__(self, max_tokens=2_000_000, max_requests=2000):
        self.max_tokens, self.max_requests = max_tokens, max_requests
        self.tokens = self.requests = 0
        self.usage = {'chat_input': 0, 'chat_output': 0, 'embedding_input': 0}

    def reserve(self, input_tokens_upper_bound, output_tokens=0):
        if (self.requests >= self.max_requests
                or self.tokens + input_tokens_upper_bound + output_tokens > self.max_tokens):
            raise BudgetExceeded('Configured API budget exhausted before the next request')
        self.requests += 1

    def complete(self, *, chat_input=0, chat_output=0, embedding_input=0):
        for key, value in locals().copy().items():
            if key != 'self':
                self.usage[key] += value
                self.tokens += value

    def describe(self):
        return {'requests': self.requests, 'tokens': self.tokens, **self.usage,
                'max_tokens': self.max_tokens, 'max_requests': self.max_requests}


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.calls, self.pending, self.latest = [], {}, {}
        self.round, self.phase = 0, 'bootstrap'
        self.record({'type': 'trace_started'})

    def record(self, value):
        with self.path.open('a') as handle:
            handle.write(json.dumps(value, allow_nan=False) + '\n')

    def expose(self, target, sources):
        self.pending[target] = deepcopy(sources)

    def append_call(self, agent, messages, response, elapsed, model, usage, finish_reason):
        call = {'type': 'model_call', 'sequence': len(self.calls) + 1,
                'agent': agent, 'round': self.round, 'phase': self.phase,
                'messages': deepcopy(messages), 'response': response,
                'sources': self.pending.pop(agent, []), 'latency_seconds': elapsed,
                'model': model, 'usage': usage, 'finish_reason': finish_reason}
        self.calls.append(call)
        self.latest[agent] = {'agent': agent, 'sequence': call['sequence'], 'response': response}
        self.record(call)


class RecordingClient:
    """Intercept actual upstream Chat Completions calls, including injected prompts."""

    def __init__(self, client, trace, agent, budget):
        self.client, self.trace, self.agent, self.budget = client, trace, agent, budget
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        # UTF-8 byte length + message overhead is a conservative tokenizer bound.
        upper = len(json.dumps(kwargs['messages'], ensure_ascii=False).encode()) + 32 * len(kwargs['messages'])
        self.budget.reserve(upper, kwargs.get('max_tokens', 1024))
        started = time.perf_counter()
        response = self.client.chat.completions.create(**kwargs)
        elapsed = time.perf_counter() - started
        usage = {'input_tokens': response.usage.prompt_tokens, 'output_tokens': response.usage.completion_tokens}
        self.budget.complete(chat_input=usage['input_tokens'], chat_output=usage['output_tokens'])
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Agent returned no usable text')
        self.trace.append_call(self.agent, kwargs['messages'], content, elapsed, response.model,
                               usage, response.choices[0].finish_reason)
        return response


class EmbeddingClient:
    def __init__(self, client, budget):
        self.client, self.budget, self.seconds = client, budget, 0.0
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.budget.reserve(sum(len(text.encode()) for text in kwargs['input']))
        started = time.perf_counter()
        response = self.client.embeddings.create(**kwargs)
        self.seconds += time.perf_counter() - started
        self.budget.complete(embedding_input=response.usage.total_tokens)
        return response


def observed_debate(base_class, trace):
    class ObservedDebate(base_class):
        def _construct_message(self, current_agent_idx, query):
            target = f'debater_{current_agent_idx}'
            trace.expose(target, [trace.latest[f'debater_{i}'] for i in range(self.agents_num)
                                  if i != current_agent_idx and f'debater_{i}' in trace.latest])
            return super()._construct_message(current_agent_idx, query)

        def conclude(self, args):
            trace.expose('aggregator', [trace.latest[f'debater_{i}'] for i in range(self.agents_num)])
            return super().conclude(args)

    return ObservedDebate


def name_marker(text):
    value = text.lower()
    return 'david tao' in value or 'davidtao' in value
