import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from contracts.manifest import read_manifest
from contracts.models import GatewayCall, GatewayResult
from gateway.mcp_client import call_mcp_tool_sync
from gateway.policy import GatewayPolicy, audit_authentication_refusal
from settings import (
    AGENT_TOKENS,
    GATEWAY_PORT,
    PAYMENTS_MCP_PORT,
    RECONCILIATION_MCP_PORT,
    VAR,
)

MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH", VAR / "session.json"))
AUDIT_PATH = Path(os.environ.get("AUDIT_LOG_PATH", VAR / "audit.jsonl"))
PAYMENTS_MCP_URL = os.environ.get("PAYMENTS_MCP_URL", f"http://127.0.0.1:{PAYMENTS_MCP_PORT}/mcp")
RECONCILIATION_MCP_URL = os.environ.get(
    "RECONCILIATION_MCP_URL", f"http://127.0.0.1:{RECONCILIATION_MCP_PORT}/mcp"
)
TOOL_URLS = {
    "get_processing": PAYMENTS_MCP_URL,
    "get_reconciliation": RECONCILIATION_MCP_URL,
}

app = FastAPI(title="MCP Gateway")
_policy: GatewayPolicy | None = None
_catalog_signature: str | None = None


def _policy_for_manifest() -> GatewayPolicy:
    global _policy, _catalog_signature
    manifest = read_manifest(MANIFEST_PATH)
    signature = manifest.model_dump_json()
    if _policy is None or signature != _catalog_signature:
        _policy = GatewayPolicy(manifest.ids(), AUDIT_PATH)
        _catalog_signature = signature
    return _policy


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="bearer ausente")
    token = authorization.removeprefix("Bearer ").strip()
    agent_id = AGENT_TOKENS.get(token)
    if agent_id is None:
        raise HTTPException(status_code=401, detail="bearer desconhecido")
    return agent_id


def _downstream(tool: str, arguments: dict):
    url = TOOL_URLS.get(tool)
    if url is None:
        raise RuntimeError(f"ferramenta sem servidor MCP: {tool}")
    return call_mcp_tool_sync(url, tool, arguments)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/tools/call")
def call_tool(
    body: GatewayCall,
    authorization: str | None = Header(default=None),
) -> GatewayResult:
    try:
        agent_id = _bearer(authorization)
    except HTTPException as exc:
        if exc.status_code == 401:
            audit_authentication_refusal(AUDIT_PATH, body)
        raise
    status, result = _policy_for_manifest().handle(agent_id, body, _downstream)
    if status != 200:
        raise HTTPException(status_code=status, detail=result.model_dump(mode="json"))
    return result


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", GATEWAY_PORT)))


if __name__ == "__main__":
    main()
