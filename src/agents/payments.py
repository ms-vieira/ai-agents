import os

import uvicorn
from fastapi import FastAPI

from agents.runtime import GATEWAY_URL, build_card, mount_cards, run_domain_task, GatewayClient
from contracts.models import DomainTask, SpecialistArtifact
from settings import PAYMENTS_AGENT_PORT

TOKEN = os.environ.get("PAYMENTS_AGENT_TOKEN", "dev-payments")
PUBLIC_URL = os.environ.get("PAYMENTS_AGENT_URL", f"http://127.0.0.1:{PAYMENTS_AGENT_PORT}/v1/tasks")


def create_app() -> FastAPI:
    app = FastAPI(title="Payments")
    mount_cards(
        app,
        build_card(
            "Payments",
            "Reads whether the customer payment was processed on the business date.",
            PUBLIC_URL,
            "lookup_customer_processing",
            "Look up processing",
            "Returns the payment processing status for one customer from the replica.",
        ),
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": "payments"}

    @app.post("/v1/tasks", response_model=SpecialistArtifact)
    def tasks(task: DomainTask) -> SpecialistArtifact:
        client = GatewayClient(os.environ.get("GATEWAY_URL", GATEWAY_URL), TOKEN)
        try:
            return run_domain_task("payments", "get_processing", task, client)
        finally:
            client.close()

    return app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", PAYMENTS_AGENT_PORT)))


if __name__ == "__main__":
    main()
