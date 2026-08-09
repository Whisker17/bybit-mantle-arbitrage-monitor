"""Read-only FastAPI surface over the collector journal (WHI-757 / WHI-979).

Reuses ``JournalReader`` + TUI builders + M3/M4 math — no business-logic rewrite.
Deployed as ``python -m monitor.api`` (uvicorn, single worker) on ``127.0.0.1``;
optional ``static_dir`` serves the Next export same-origin (no nginx required —
see ``docs/adr/0003-same-origin-static-panel.md``).
"""

from monitor.api.app import create_app
from monitor.api.config import ApiConfig, ApiConfigError, load_api_config

__all__ = [
    "ApiConfig",
    "ApiConfigError",
    "create_app",
    "load_api_config",
]
