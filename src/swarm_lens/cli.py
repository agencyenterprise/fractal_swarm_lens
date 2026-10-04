import argparse
from importlib import import_module
from pathlib import Path

from .application.framework import Framework
from .adapters.artifacts import FileArtifacts
from .adapters.git import GitVersions
from .adapters.sqlite import SQLiteHistory
from .plugins import ActivityPlugin


def load_factory(spec: str):
    """Resolve a trusted MODULE:FACTORY reference given on the command line."""
    module, separator, name = spec.partition(":")
    if not separator:
        raise ValueError(f"Expected MODULE:FACTORY, got {spec!r}")
    return getattr(import_module(module), name)


def build_app(data: Path, runtime_specs=(), trace_tools_spec: str | None = None):
    """The explorer's composition root: history stores, live execution, and the MAST web plugin."""
    from .integrations.crewai.continuation import TraceCrewAIRuntime
    from .live.service import LiveService
    from .observability.mast import MastPlugin
    from .observability.mast.judge import OpenAIMastJudge
    from .observability.mast.service import MastJobs, MastService
    from .web.api import create_app
    from .web.plugins.mast import mast_extension

    framework = Framework(SQLiteHistory(data / "history.sqlite"),
                          versions=GitVersions(data / "history.git"), plugins=(ActivityPlugin(),))
    artifacts = FileArtifacts(data / "artifacts")
    runtimes = [load_factory(spec)() for spec in runtime_specs]
    tools = load_factory(trace_tools_spec)() if trace_tools_spec else {}
    runtimes.append(TraceCrewAIRuntime(framework, tools=tools))
    live = LiveService(framework, data / "live.sqlite", tuple(runtimes))
    mast = MastService(framework, MastPlugin(OpenAIMastJudge()), MastJobs(data / "mast.sqlite"), artifacts)
    return create_app(framework, artifacts, extensions=(mast_extension(mast),), live=live)


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

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # Environment variables work without the optional dotenv package.
    else:
        load_dotenv(args.env_file, override=False)

    try:
        app = build_app(args.data, args.runtime, args.trace_tools)
    except ValueError as error:
        parser.error(str(error))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
