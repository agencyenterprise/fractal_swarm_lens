"""HTTP calls per website over session time: a model-free saved-trace analysis."""
from .method import HttpCallsConfig, analyze_http_calls


class HttpCallsPlugin:
    id, version = "http_calls", "1.0.0"

    def run(self, context, config):
        return analyze_http_calls(context.history(), config)


__all__ = ["HttpCallsConfig", "HttpCallsPlugin", "analyze_http_calls"]
