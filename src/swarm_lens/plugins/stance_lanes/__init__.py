"""Stance lanes plugin: the analyzer plus its lanes view (assets/index.js). See docs/plugins/stance-lanes.md."""
from pathlib import Path

from fastapi import APIRouter

from swarm_lens.web.extensions import PluginServices, WebExtension
from .labeller import LLMStanceLabeller
from .method import Params, StanceLanes, normalize_label

__all__ = ["LLMStanceLabeller", "Params", "StanceLanes", "create", "normalize_label"]


def create(services: PluginServices):
    analyzer = StanceLanes(LLMStanceLabeller(services.data / "stance-lanes" / "llm-cache"))
    view = WebExtension("stance-lanes", APIRouter(prefix="/api/plugins/stance-lanes"),
                        lambda: {"title": StanceLanes.title, "version": StanceLanes.version},
                        assets=Path(__file__).parent / "assets")
    return [analyzer, view]
