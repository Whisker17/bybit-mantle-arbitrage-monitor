"""Read-only FastAPI surface over the collector journal (WHI-757).

Reuses ``JournalReader`` + TUI builders + M3/M4 math — no business-logic rewrite.
Deployed as ``python -m monitor.api`` (uvicorn, single worker) behind nginx.
"""

from monitor.api.app import create_app

__all__ = ["create_app"]
