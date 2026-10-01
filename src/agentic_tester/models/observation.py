"""Observable browser state captured around tester actions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .element import ElementRef


class ConsoleEntry(BaseModel):
    """One browser console message."""

    level: Literal["log", "warn", "error", "info", "debug"]
    text: str
    timestamp: datetime


class NetworkEntry(BaseModel):
    """One observed network request/response or failed request."""

    method: str
    url: str
    status: int | None
    response_body_preview: str | None
    duration_ms: int | None
    is_failure: bool


class PageObservation(BaseModel):
    """Complete page snapshot used by the action loop and oracle."""

    url: str
    title: str
    screenshot_path: str
    accessibility_tree: str
    interactive_elements: list[ElementRef]
    visible_text_summary: str
    console_entries: list[ConsoleEntry]
    network_log: list[NetworkEntry]
    state_fingerprint: str
    timestamp: datetime
