"""Reconciliation read API. Serves the reconciliation replica."""

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn

from replica.api import create_read_app
from settings import RECONCILIATION_API_PORT, VAR

DB_PATH = Path(os.environ.get("RECONCILIATION_DB", VAR / "reconciliation.sqlite"))


def main() -> None:
    app = create_read_app(DB_PATH, "reconciliation")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", RECONCILIATION_API_PORT)))


if __name__ == "__main__":
    main()
