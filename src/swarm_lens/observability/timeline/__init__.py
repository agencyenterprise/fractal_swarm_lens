"""Timelines of relevant events: one long-context call, a bottom-up tree, or a top-down goal tree."""
from .method import TimelineConfig, TraceTooLarge, analyze


class TimelinePlugin:
    id, version = "timeline", "0.3.0"

    def __init__(self, llm):
        self.llm = llm

    def run(self, context, config):
        return analyze(context.history(), self.llm, TimelineConfig.from_dict(config))


__all__ = ["TimelineConfig", "TimelinePlugin", "TraceTooLarge", "analyze"]
