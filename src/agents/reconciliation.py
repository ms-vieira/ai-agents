import os

import uvicorn
from fastapi import FastAPI

from agents.runtime import GATEWAY_URL, GatewayClient, build_card, mount_cards, run_domain_task
from contracts.models import DomainTask, SpecialistArtifact
from settings import RECONCILIATION_AGENT_PORT

TOKEN = os.environ.get("RECONCILIATION_AGENT_TOKEN", "dev-reconciliation")
PUBLIC_URL = os.environ.get(
    "RECONCILIATION_AGENT_URL", f"http://127.0.0.1:{RECONCILIATION_AGENT_PORT}/v1/tasks"
)


def create_app() -> FastAPI:
    app = FastAPI(title="Reconciliation")
    mount_cards(
        app,
        build_card(
            "Reconciliation",
            "Reads whether the customer reconciliation is updated, pending, or in error.",
            PUBLIC_URL,
            "lookup_customer_reconciliation",
            "Look up reconciliation",
            "Returns the reconciliation status for one customer from the replica.",
        ),
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": "reconciliation"}

    @app.post("/v1/tasks", response_model=SpecialistArtifact)
    def tasks(task: DomainTask) -> SpecialistArtifact:
        client = GatewayClient(os.environ.get("GATEWAY_URL", GATEWAY_URL), TOKEN)
        try:
            return run_domain_task("reconciliation", "get_reconciliation", task, client)
        finally:
            client.close()

    return app


def main() -> None:
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", RECONCILIATION_AGENT_PORT)),
    )


if __name__ == "__main__":
    main()
