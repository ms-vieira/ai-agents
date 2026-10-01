"""Servidores MCP. Cada um chama só o GET da API de leitura do seu domínio."""

import json
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from settings import READ_TIMEOUT_SECONDS


def fetch_json(
    base_url: str,
    path: str,
    params: dict[str, str],
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.Client(base_url=base_url, timeout=READ_TIMEOUT_SECONDS)
    try:
        response = http.get(path, params=params)
        response.raise_for_status()
        body = response.json()
    finally:
        if owns_client:
            http.close()
    if not isinstance(body, dict):
        raise RuntimeError("a API de leitura não devolveu um objeto")
    return body


def build_payments_server(base_url: str) -> MCPServer:
    server = MCPServer(
        "payments",
        instructions="Read the payments replica for one customer. Do not write and do not list.",
    )

    @server.tool()
    def get_processing(customer_id: str, business_date: str) -> dict[str, Any]:
        """Read the payments replica for one customer and business date."""
        return fetch_json(
            base_url,
            f"/v1/customers/{customer_id}/processing",
            {"business_date": business_date},
        )

    return server


def build_reconciliation_server(base_url: str) -> MCPServer:
    server = MCPServer(
        "reconciliation",
        instructions="Read the reconciliation replica for one customer. Do not write and do not list.",
    )

    @server.tool()
    def get_reconciliation(customer_id: str, business_date: str) -> dict[str, Any]:
        """Read the reconciliation replica for one customer and business date."""
        return fetch_json(
            base_url,
            f"/v1/customers/{customer_id}/reconciliation",
            {"business_date": business_date},
        )

    return server


def tool_result_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise RuntimeError("a ferramenta MCP devolveu erro")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError("a ferramenta MCP não devolveu JSON")
