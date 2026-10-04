"""The MAST web plugin: report routes, plus its browser module in static/."""
import logging

from swarm_lens.core.models import DomainError
from swarm_lens.web.extensions import PluginServices, WebExtension
from .extension import mast_extension

__all__ = ["create", "mast_extension"]
log = logging.getLogger(__name__)


def create(services: PluginServices) -> WebExtension:
    """The MAST report view with the OpenAI judge; its jobs and findings live in the shared plugin service.

    Jobs from before the shared store (data/mast.sqlite) are copied in on start; the old file is only read.
    """
    from swarm_lens.observability.mast import MastPlugin
    from swarm_lens.observability.mast.judge import OpenAIMastJudge
    from swarm_lens.observability.mast.service import MastService

    service = MastService(services.plugins, MastPlugin(OpenAIMastJudge()), services.artifacts)
    service.jobs.import_records(legacy_jobs(services, service.jobs))
    return mast_extension(service)


def legacy_jobs(services: PluginServices, jobs) -> list[dict]:
    """The old store's jobs not yet imported, upgraded; a job that cannot be upgraded is logged and skipped."""
    from swarm_lens.adapters.jobs import read_jobs
    from swarm_lens.observability.mast.service import upgrade_job

    upgraded = []
    for job in read_jobs(services.data / "mast.sqlite", "mast_jobs"):
        try:
            jobs.get(job["id"])
            continue
        except DomainError:
            pass
        try:
            history = services.framework.history(job["branch_id"], job["cursor"])
            upgraded.append(upgrade_job(job, {event.id for event in history}))
        except Exception as exc:
            log.warning("Skipped MAST job %s from mast.sqlite: %s", job.get("id"), exc)
    return upgraded
