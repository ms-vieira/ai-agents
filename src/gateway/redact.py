import re
from typing import Any

_SECRET = re.compile(r"sk_live_[A-Za-z0-9]+")
_TOKEN_ASSIGNMENT = re.compile(r"token=\S+")


def redact_text(value: str) -> str:
    value = _SECRET.sub("[REDACTED]", value)
    return _TOKEN_ASSIGNMENT.sub("token=[REDACTED]", value)


def redact_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return _walk(payload)


def _walk(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_walk(item) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item) for key, item in value.items()}
    return value


def redacted_excerpt(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    payment = payload.get("payment") or {}
    reconciliation = payload.get("reconciliation") or {}
    text = payment.get("error_detail") or reconciliation.get("detail")
    if not isinstance(text, str) or text == "":
        return None
    return text
