import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from carga.load import read_manifest
from orchestrator.loop import HttpAgentDirectory, answer_question
from settings import (
    ANOMALY_AGENT_PORT,
    ORCHESTRATOR_PORT,
    PAYMENTS_AGENT_PORT,
    RECONCILIATION_AGENT_PORT,
    VAR,
)

MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH", VAR / "session.json"))
PAYMENTS_URL = os.environ.get("PAYMENTS_AGENT_URL", f"http://127.0.0.1:{PAYMENTS_AGENT_PORT}")
RECONCILIATION_URL = os.environ.get(
    "RECONCILIATION_AGENT_URL", f"http://127.0.0.1:{RECONCILIATION_AGENT_PORT}"
)
ANOMALY_URL = os.environ.get("ANOMALY_AGENT_URL", f"http://127.0.0.1:{ANOMALY_AGENT_PORT}")

app = FastAPI(title="Orquestrador")
_directory: HttpAgentDirectory | None = None


class QuestionBody(BaseModel):
    question: str


def directory() -> HttpAgentDirectory:
    global _directory
    if _directory is None:
        _directory = HttpAgentDirectory(PAYMENTS_URL, RECONCILIATION_URL, ANOMALY_URL)
    return _directory


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/catalog")
def catalog() -> dict:
    manifest = read_manifest(MANIFEST_PATH)
    public = manifest.public()
    return {"kind": "catalog", "catalog": public.model_dump(mode="json"), "answer": None}


@app.post("/v1/perguntar")
def perguntar(body: QuestionBody) -> dict:
    manifest = read_manifest(MANIFEST_PATH)
    return answer_question(body.question, manifest, directory())


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", ORCHESTRATOR_PORT)))


if __name__ == "__main__":
    main()
