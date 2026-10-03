"""Autoriza a chamada de ferramenta antes de qualquer ida à API de leitura."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from contracts.models import AuditLine, GatewayCall, GatewayResult, ToolArguments
from gateway.redact import redact_payload, redacted_excerpt
from settings import ALLOWLIST, MAX_TOOL_CALLS_PER_TASK, RATE_LIMIT_CAPACITY, RATE_LIMIT_WINDOW_SECONDS


class GatewayPolicy:
    def __init__(
        self,
        catalog_ids: set[str],
        audit_path: Path,
        *,
        max_calls: int = MAX_TOOL_CALLS_PER_TASK,
        rate_capacity: int = RATE_LIMIT_CAPACITY,
        rate_window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.catalog_ids = catalog_ids
        self.audit_path = audit_path
        self.max_calls = max_calls
        self.rate_capacity = rate_capacity
        self.rate_window_seconds = rate_window_seconds
        self._counts: dict[tuple[str, str], int] = {}
        self._cache: dict[tuple, dict] = {}
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def handle(
        self,
        agent_id: str,
        call: GatewayCall,
        downstream,
        *,
        now: float | None = None,
    ) -> tuple[int, GatewayResult]:
        clock = time.monotonic() if now is None else now
        started = datetime.now(timezone.utc)
        decision, data, detail = self._decide(agent_id, call, downstream, clock)
        result = GatewayResult(decision=decision, data=data, detail=detail)
        self._audit(agent_id, call, result, started)
        return _status_for(decision), result

    def _decide(self, agent_id: str, call: GatewayCall, downstream, now: float):
        allowed_tools = ALLOWLIST.get(agent_id, frozenset())
        if call.tool not in allowed_tools:
            return "denied_allowlist", None, "ferramenta fora da allowlist do agente"

        try:
            arguments = ToolArguments.model_validate(call.arguments)
        except ValidationError:
            return "denied_missing_customer", None, "customer_id e business_date são obrigatórios"

        if not call.task_customer_id:
            return "denied_missing_customer", None, "a tarefa não tem customer_id"

        if arguments.customer_id != call.task_customer_id:
            return "denied_customer_mismatch", None, "o customer da ferramenta difere do customer da tarefa"

        if arguments.customer_id not in self.catalog_ids:
            return "denied_customer_not_in_catalog", None, "customer fora da carga desta sessão"

        budget_key = (call.task_id, agent_id)
        if self._counts.get(budget_key, 0) >= self.max_calls:
            return "budget_exhausted", None, "a tarefa atingiu o teto de chamadas"

        replay_key = (
            call.trace_id,
            call.task_id,
            agent_id,
            call.tool,
            arguments.customer_id,
            arguments.business_date.isoformat(),
        )
        cached = self._cache.get(replay_key)
        if cached is not None:
            self._counts[budget_key] = self._counts.get(budget_key, 0) + 1
            return "replayed", cached, "resultado reaproveitado sem nova ida à API"

        if not self._take_token((agent_id, call.tool), now):
            return "rate_limited", None, "limite de chamadas por minuto"

        try:
            raw = downstream(call.tool, arguments.model_dump(mode="json"))
        except Exception:
            return "downstream_failed", None, "a ferramenta não respondeu"
        redacted = redact_payload(raw) or {}
        self._cache[replay_key] = redacted
        self._counts[budget_key] = self._counts.get(budget_key, 0) + 1
        return "allowed", redacted, None

    def _take_token(self, key: tuple[str, str], now: float) -> bool:
        rate = self.rate_capacity / self.rate_window_seconds
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = (self.rate_capacity - 1, now)
            return True
        tokens, updated = bucket
        tokens = min(self.rate_capacity, tokens + (now - updated) * rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True

    def _audit(self, agent_id: str, call: GatewayCall, result: GatewayResult, started: datetime) -> None:
        finished = datetime.now(timezone.utc)
        line = AuditLine(
            trace_id=call.trace_id,
            task_id=call.task_id,
            agent_id=agent_id,
            model=call.model,
            tool=call.tool,
            customer_id=call.task_customer_id,
            decision=result.decision,
            latency_ms=int((finished - started).total_seconds() * 1000),
            at=finished,
            redacted_text=redacted_excerpt(result.data),
        )
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line.model_dump_json() + "\n")


def audit_authentication_refusal(audit_path: Path, call: GatewayCall) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    line = AuditLine(
        trace_id=call.trace_id,
        task_id=call.task_id,
        agent_id="unknown",
        model=call.model,
        tool=call.tool,
        customer_id=call.task_customer_id,
        decision="denied_authentication",
        latency_ms=0,
        at=datetime.now(timezone.utc),
        redacted_text=None,
    )
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(line.model_dump_json() + "\n")


def _status_for(decision: str) -> int:
    if decision in {"allowed", "replayed"}:
        return 200
    if decision == "rate_limited":
        return 429
    if decision == "downstream_failed":
        return 503
    return 403


def read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            lines.append(json.loads(raw))
    return lines
