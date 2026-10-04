"""The MAST web plugin: report routes, plus its browser module in static/."""
from swarm_lens.web.extensions import PluginServices, WebExtension
from .extension import mast_extension

__all__ = ["create", "mast_extension"]


def create(services: PluginServices) -> WebExtension:
    """The MAST report view with the OpenAI judge; its jobs and findings live in the shared plugin service.

    Jobs from before the shared store (data/mast.sqlite) are copied in on every start; the old file is only read.
    """
    from swarm_lens.adapters.jobs import read_jobs
    from swarm_lens.observability.mast import MastPlugin
    from swarm_lens.observability.mast.judge import OpenAIMastJudge
    from swarm_lens.observability.mast.service import MastService, upgrade_job

    service = MastService(services.plugins, MastPlugin(OpenAIMastJudge()), services.artifacts)
    service.jobs.import_records(
        upgrade_job(job, {event.id for event in services.framework.history(job["branch_id"], job["cursor"])})
        for job in read_jobs(services.data / "mast.sqlite", "mast_jobs"))
    return mast_extension(service)
