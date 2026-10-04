"""The MAST web plugin: report routes, plus its browser module in static/."""
from swarm_lens.web.extensions import PluginServices, WebExtension
from .extension import mast_extension

__all__ = ["create", "mast_extension"]


def create(services: PluginServices) -> WebExtension:
    """The MAST plugin with the OpenAI judge and its job store under the data directory."""
    from swarm_lens.observability.mast import MastPlugin
    from swarm_lens.observability.mast.judge import OpenAIMastJudge
    from swarm_lens.observability.mast.service import MastJobs, MastService

    service = MastService(services.framework, MastPlugin(OpenAIMastJudge()),
                          MastJobs(services.data / "mast.sqlite"), services.artifacts)
    return mast_extension(service)
