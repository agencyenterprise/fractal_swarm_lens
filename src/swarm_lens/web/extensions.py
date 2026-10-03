"""FastAPI-specific extensions stay outside the domain and application layers."""
from dataclasses import dataclass
from collections.abc import Callable
from fastapi import APIRouter


@dataclass
class WebExtension:
    id: str
    router: APIRouter
    manifest: Callable[[], dict]
