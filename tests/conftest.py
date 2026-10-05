import pytest


@pytest.fixture(autouse=True)
def _strip_openai_key(monkeypatch):
    """The suite must not call the real model provider, even if a key is exported."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
