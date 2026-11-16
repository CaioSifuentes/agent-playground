"""Square Cloud / local entrypoint.

Square Cloud runs:
    python -m uvicorn main:app --host 0.0.0.0 --port 80

Locally:
    python main.py            # http://localhost:8000
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os

import uvicorn

from app.main import app  # noqa: F401  (exported for `uvicorn main:app`)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
