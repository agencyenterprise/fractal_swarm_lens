"""FastAPI-specific extensions stay outside the domain and application layers."""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re

from fastapi import APIRouter

from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.application.framework import Framework

PLUGIN_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
PLUGIN_ENTRY = "index.js"


@dataclass(frozen=True)
class PluginServices:
    """What the host gives a web plugin factory: `factory(services) -> WebExtension`."""
    framework: Framework
    artifacts: FileArtifacts
    data: Path


@dataclass(frozen=True)
class WebExtension:
    """A trusted web plugin: routes under /api/plugins/{id}/, a manifest, and an optional browser module.

    `assets` is a directory of public browser files served at /assets/plugins/{id}/. Its index.js exports
    `install(host, manifest)`, which the explorer calls once at startup.
    """
    id: str
    router: APIRouter
    manifest: Callable[[], dict]
    assets: Path | None = None

    def __post_init__(self):
        if not PLUGIN_ID.fullmatch(self.id):
            raise ValueError(f"Plugin ID must be lowercase letters, digits and hyphens: {self.id!r}")
        if self.assets is not None and not (Path(self.assets) / PLUGIN_ENTRY).is_file():
            raise ValueError(f"Plugin {self.id} declares assets without {PLUGIN_ENTRY}: {self.assets}")

    @property
    def api_prefix(self) -> str:
        return f"/api/plugins/{self.id}"

    @property
    def assets_url(self) -> str:
        return f"/assets/plugins/{self.id}"

    def describe(self) -> dict:
        """The plugin's manifest with the fields the host owns, so a plugin cannot misstate its own mount points."""
        ui = {"module": f"{self.assets_url}/{PLUGIN_ENTRY}"} if self.assets is not None else None
        return {**self.manifest(), "id": self.id, "api_prefix": self.api_prefix, "ui": ui}
