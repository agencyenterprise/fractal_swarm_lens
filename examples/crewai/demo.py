"""A real three-task CrewAI crew; --offline uses a deterministic test model."""
import argparse
import os

# This example sends observability only to the configured SwarmLens server.
os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
os.environ.setdefault('CREWAI_TELEMETRY_DISABLED', 'true')
os.environ.setdefault('CREWAI_TRACING_ENABLED', 'false')

from crewai import Agent, Crew, LLM, Process, Task
from crewai.llms.base_llm import BaseLLM
from crewai.tools import tool
from swarm_lens.integrations.crewai import CrewAIRuntime, observe


@tool('add_values')
def add_values(a: int, b: int) -> int:
    """Add two integers using Python."""
    return a+b


class OfflineLLM(BaseLLM):
    """Compatibility fixture; CrewAI still executes its real task/tool loop."""
    def __init__(self):
        super().__init__(model='swarm-lens-offline-fixture', temperature=0)

    def supports_function_calling(self):
        return False

    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        prompt = str(messages)
        used_tool = any(m.get('role') == 'assistant' and 'Action: add_values' in str(m.get('content')) for m in messages)
        if 'Use add_values' in prompt and not used_tool:
            return 'Thought: I will calculate the sum.\nAction: add_values\nAction Input: {"a": 19, "b": 23}'
        if 'violet' in prompt.lower():
            return 'Final Answer: violet: 42'
        return 'Final Answer: 42'


def make_crew(inputs):
    model = inputs.get('model', 'gpt-5.5')
    def llm():
        return OfflineLLM() if model == 'offline' else LLM(model=model, timeout=90, max_completion_tokens=2048)
    analyst = Agent(role='Calculator', goal='Calculate precisely', backstory='Use the available calculator tool.',
                    llm=llm(), tools=[add_values], allow_delegation=False, max_iter=4, max_retry_limit=0)
    reviewer = Agent(role='Reviewer', goal='Check the calculation', backstory='Verify the preceding task output.',
                     llm=llm(), allow_delegation=False, max_iter=3, max_retry_limit=0)
    writer = Agent(role='Reporter', goal='Report the verified result', backstory='Return one concise sentence.',
                   llm=llm(), allow_delegation=False, max_iter=3, max_retry_limit=0)
    tasks = [Task(description='Use add_values to calculate 19 + 23. Return the sum.', expected_output='The sum.', agent=analyst),
             Task(description='Verify the preceding sum of 19 + 23. Explain briefly.', expected_output='A verified answer.', agent=reviewer),
             Task(description='Write the verified result in one sentence.', expected_output='One sentence with the result.', agent=writer)]
    return Crew(name='CrewAI arithmetic example', agents=[analyst,reviewer,writer], tasks=tasks,
                process=Process.sequential, memory=False, cache=False, tracing=False, verbose=False)


def create_runtime():
    return CrewAIRuntime('arithmetic-demo', make_crew, revision='arithmetic-demo-v1',
                        launch_inputs={'model': 'gpt-5.5'}, title='CrewAI arithmetic · GPT-5.5 live',
                        description='Three agents calculate 19 + 23 with a Python tool, verify the answer, and write a report. Uses GPT-5.5 through your configured OpenAI API key.',
                        required_env=('OPENAI_API_KEY',))


def trace_tools():
    """Map imported tool names to fresh, host-owned executable implementations."""
    from crewai.tools.base_tool import Tool
    return {'add_values': lambda: Tool(name='add_values', description='Add two integers using Python.',
                                       func=add_values.func, args_schema=add_values.args_schema)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    inputs = {'model': 'offline' if args.offline else 'gpt-5.5'}
    crew = make_crew(inputs)
    with observe(crew, inputs=inputs, runtime=create_runtime(), url=args.url,
                 name='CrewAI arithmetic · '+('offline compatibility test' if args.offline else 'GPT-5.5 live')) as capture:
        output = crew.kickoff(inputs=inputs)
    print('Result:', output.raw)
    print('Branch:', capture.branch_id)
    print('Outbox:', capture.path)


if __name__ == '__main__':
    main()
