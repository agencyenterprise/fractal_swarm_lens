import argparse
from pathlib import Path

from .application.framework import Framework
from .adapters.artifacts import FileArtifacts
from .adapters.git import GitVersions
from .adapters.sqlite import SQLiteHistory
from .plugins import ActivityPlugin
from .observability.http_calls import HttpCallsPlugin


def main():
    parser = argparse.ArgumentParser(description="Explore a framework application's recorded runs")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--runtime", action="append", default=[], metavar="MODULE:FACTORY",
                        help="Trusted local factory returning a CrewAIRuntime (repeatable)")
    parser.add_argument("--trace-tools", metavar="MODULE:FACTORY",
                        help="Trusted factory returning recorded tool name → fresh CrewAI tool factory mappings")
    args = parser.parse_args()
    import uvicorn
    from .web.api import create_app
    from .web.http_calls import http_calls_extension
    from .web.mast import mast_extension
    from .web.timeline_report import TimelineService, timeline_extension
    from .adapters.jobs import JobStore
    from .observability.mast import MastPlugin
    from .observability.mast.judge import OpenAIMastJudge
    from .observability.mast.service import MastService
    from .adapters.openai_chat import OpenAIChat
    from .observability.timeline import TimelinePlugin

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # Environment variables work without the optional dotenv package.
    else:
        load_dotenv(args.env_file, override=False)

    framework = Framework(SQLiteHistory(args.data / "history.sqlite"),
                          versions=GitVersions(args.data / "history.git"),
                          plugins=(ActivityPlugin(), HttpCallsPlugin(), TimelinePlugin(OpenAIChat(
                              env_prefix="TIMELINE", extra="timeline", default_model="gpt-5.6-sol",
                              max_completion_tokens=32_768, max_retries=2))))
    artifacts = FileArtifacts(args.data / "artifacts")
    from importlib import import_module
    from .live.service import LiveService
    runtimes = []
    for spec in args.runtime:
        module, separator, factory = spec.partition(":")
        if not separator:
            parser.error("--runtime must be MODULE:FACTORY")
        runtimes.append(getattr(import_module(module), factory)())
    from .integrations.crewai.continuation import TraceCrewAIRuntime
    tools = {}
    if args.trace_tools:
        module, separator, factory = args.trace_tools.partition(":")
        if not separator:
            parser.error("--trace-tools must be MODULE:FACTORY")
        tools = getattr(import_module(module), factory)()
    runtimes.append(TraceCrewAIRuntime(framework, tools=tools))
    live = LiveService(framework, args.data / "live.sqlite", tuple(runtimes))
    mast = MastService(framework, MastPlugin(OpenAIMastJudge()),
                       JobStore(args.data / "mast.sqlite", "mast", "MAST"), artifacts)
    timeline = TimelineService(framework, JobStore(args.data / "timeline.sqlite", "timeline", "timeline"))
    uvicorn.run(create_app(framework, artifacts, extensions=(mast_extension(mast), timeline_extension(timeline),
                                                       http_calls_extension(framework)),
                           live=live),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
