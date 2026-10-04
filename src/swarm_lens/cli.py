import argparse
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path

from .application.framework import Framework
from .application.plugins import PluginService
from .adapters.artifacts import FileArtifacts
from .adapters.git import GitVersions
from .adapters.jobs import JobStore
from .adapters.sqlite import SQLiteHistory


def load_factory(spec: str):
    """Resolve a trusted MODULE:FACTORY reference given on the command line."""
    module, separator, name = spec.partition(":")
    if not separator:
        raise ValueError(f"Expected MODULE:FACTORY, got {spec!r}")
    return getattr(import_module(module), name)


PLUGIN_GROUP = "swarm_lens.plugins"
BUNDLED_PLUGINS = ("swarm_lens.plugins.activity:create", "swarm_lens.web.plugins.mast:create",
                   "swarm_lens.plugins.spread_tracer:create")


def plugin_factories(plugin_specs=()):
    """Bundled plugins, then installed ones (entry point group `swarm_lens.plugins`), then --plugin flags."""
    return [*(load_factory(spec) for spec in BUNDLED_PLUGINS),
            *(entry.load() for entry in entry_points(group=PLUGIN_GROUP)),
            *(load_factory(spec) for spec in plugin_specs)]


def install_plugins(services, factories):
    """Register what each factory returns; a factory may return one object or a list or tuple of them."""
    from .web.extensions import WebExtension

    extensions = []
    for factory in factories:
        produced = factory(services)
        for item in produced if isinstance(produced, list | tuple) else (produced,):
            if isinstance(item, WebExtension):
                extensions.append(item)
            else:
                services.plugins.register(item)
    return extensions


def build_app(data: Path, runtime_specs=(), trace_tools_spec: str | None = None, plugin_specs=()):
    """The explorer's composition root: history stores, live execution, and plugins (bundled, installed, given)."""
    from .integrations.crewai.continuation import TraceCrewAIRuntime
    from .live.service import LiveService
    from .web.api import create_app
    from .web.extensions import PluginServices

    framework = Framework(SQLiteHistory(data / "history.sqlite"), versions=GitVersions(data / "history.git"))
    artifacts = FileArtifacts(data / "artifacts")
    plugins = PluginService(framework, jobs=lambda name, label: JobStore(data / "jobs.sqlite", name, label))
    runtimes = [load_factory(spec)() for spec in runtime_specs]
    tools = load_factory(trace_tools_spec)() if trace_tools_spec else {}
    runtimes.append(TraceCrewAIRuntime(framework, tools=tools))
    live = LiveService(framework, data / "live.sqlite", tuple(runtimes))
    extensions = install_plugins(PluginServices(framework, artifacts, data, plugins), plugin_factories(plugin_specs))
    return create_app(framework, artifacts, plugins=plugins, extensions=extensions, live=live)


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
    parser.add_argument("--plugin", action="append", default=[], metavar="MODULE:FACTORY",
                        help="Trusted plugin factory taking PluginServices and returning plugins and/or WebExtensions (repeatable)")
    args = parser.parse_args()
    import uvicorn

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass  # Environment variables work without the optional dotenv package.
    else:
        load_dotenv(args.env_file, override=False)

    try:
        app = build_app(args.data, args.runtime, args.trace_tools, args.plugin)
    except ValueError as error:
        parser.error(str(error))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
