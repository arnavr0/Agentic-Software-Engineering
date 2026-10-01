"""Thin Uvicorn entry point for the tester API."""

import uvicorn

from agentic_tester.config import Settings


def main() -> None:
    """Start the configured FastAPI application."""

    settings = Settings()
    uvicorn.run(
        "agentic_tester.api.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
