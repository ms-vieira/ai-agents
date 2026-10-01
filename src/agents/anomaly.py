import os

import uvicorn
from fastapi import FastAPI

from agents.runtime import build_card, mount_cards, run_anomaly_task
from contracts.models import AnomalyArtifact, AnomalyTask
from settings import ANOMALY_AGENT_PORT

PUBLIC_URL = os.environ.get("ANOMALY_AGENT_URL", f"http://127.0.0.1:{ANOMALY_AGENT_PORT}/v1/tasks")


def create_app() -> FastAPI:
    app = FastAPI(title="Anomaly")
    mount_cards(
        app,
        build_card(
            "Anomaly",
            "Compares the authorized payments and reconciliation reports. It does not call a system.",
            PUBLIC_URL,
            "detect_anomaly",
            "Detect anomaly",
            "Reports whether the two reports diverge, with no tool and no database.",
        ),
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": "anomaly"}

    @app.post("/v1/tasks", response_model=AnomalyArtifact)
    def tasks(task: AnomalyTask) -> AnomalyArtifact:
        return run_anomaly_task(task)

    return app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", ANOMALY_AGENT_PORT)))


if __name__ == "__main__":
    main()
