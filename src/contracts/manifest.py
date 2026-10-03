"""Read the session manifest written by app-payments."""

from pathlib import Path

from contracts.models import SessionManifest


def read_manifest(path: Path) -> SessionManifest:
    return SessionManifest.model_validate_json(path.read_text(encoding="utf-8"))
