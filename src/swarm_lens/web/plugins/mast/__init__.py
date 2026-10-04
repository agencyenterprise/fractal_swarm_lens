"""The MAST web plugin: report routes, plus its browser module in static/."""
from swarm_lens.web.extensions import PluginServices, WebExtension
from .extension import mast_extension

__all__ = ["create", "mast_extension"]


def create(services: PluginServices) -> WebExtension:
    """The MAST report view with the OpenAI judge; its jobs and findings live in the shared plugin service."""
    from swarm_lens.observability.mast import MastPlugin
    from swarm_lens.observability.mast.judge import OpenAIMastJudge
    from swarm_lens.observability.mast.service import MastService

    return mast_extension(MastService(services.plugins, MastPlugin(OpenAIMastJudge()), services.artifacts))
