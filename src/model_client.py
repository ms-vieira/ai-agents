import os

import httpx

from settings import MODEL_TIMEOUT_SECONDS


def complete(model: str, system: str, user: str, timeout: float | None = None) -> str | None:
    limit = MODEL_TIMEOUT_SECONDS if timeout is None else timeout
    if limit <= 0:
        return None
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=limit,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()
