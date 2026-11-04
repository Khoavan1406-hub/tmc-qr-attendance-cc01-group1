"""Entry layer (FastAPI controllers and dependency wiring) for the TMC Factory Gate application."""

from tmc_gate.api.app import app, create_app

__all__ = ["app", "create_app"]
