import argparse
from pathlib import Path

from .application.framework import Framework
from .adapters.artifacts import FileArtifacts
from .adapters.git import GitVersions
from .adapters.sqlite import SQLiteHistory
from .plugins import ActivityPlugin


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
    from .web.plugins.mast import mast_extension
    from .observability.mast import MastPlugin
    from .observability.mast.judge import OpenAIMastJudge
    from .observability.mast.service import MastJobs, MastService

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # Environment variables work without the optional dotenv package.
    else:
        load_dotenv(args.env_file, override=False)

    framework = Framework(SQLiteHistory(args.data / "history.sqlite"),
                          versions=GitVersions(args.data / "history.git"), plugins=(ActivityPlugin(),))
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
                       MastJobs(args.data / "mast.sqlite"), artifacts)
    uvicorn.run(create_app(framework, artifacts, extensions=(mast_extension(mast),), live=live),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
