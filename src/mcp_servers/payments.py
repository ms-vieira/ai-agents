import os

from mcp_servers.tools import build_payments_server
from settings import PAYMENTS_API_PORT, PAYMENTS_MCP_PORT

BASE_URL = os.environ.get("PAYMENTS_API_URL", f"http://127.0.0.1:{PAYMENTS_API_PORT}")
PORT = int(os.environ.get("PORT", PAYMENTS_MCP_PORT))


def main() -> None:
    server = build_payments_server(BASE_URL)
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=PORT,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
