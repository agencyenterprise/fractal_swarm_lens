"""The MAST judge: one text completion per saved trace, using the shared OpenAI chat adapter."""
from swarm_lens.adapters.openai_chat import OpenAIChat
from swarm_lens.core.models import DomainError


class OpenAIMastJudge:
    def __init__(self, *, model=None, max_completion_tokens=16_384, context_window=None):
        self.chat = OpenAIChat(env_prefix="MAST", extra="mast", model=model, context_window=context_window,
                               max_completion_tokens=max_completion_tokens, reasoning_effort="medium")

    def describe(self):
        return self.chat.describe()

    def input_budget(self, prompt):
        return self.chat.budget(prompt)

    def complete(self, prompt):
        budget = self.input_budget(prompt)
        if not budget["can_analyze"]:
            raise DomainError(budget["reason"])
        return self.chat.complete([{"role": "user", "content": prompt}])
