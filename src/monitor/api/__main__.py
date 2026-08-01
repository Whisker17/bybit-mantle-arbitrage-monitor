"""python -m monitor.api — run the read-only panel API (uvicorn)."""

from __future__ import annotations

import argparse

import uvicorn

from monitor.api.config import load_api_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="xStocks read-only panel API (WHI-757)")
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: config/api.yaml host)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: config/api.yaml port)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Dev auto-reload (never on the VPS)",
    )
    args = parser.parse_args(argv)
    cfg = load_api_config()
    host = args.host if args.host is not None else cfg.host
    port = args.port if args.port is not None else cfg.port
    # Default is a single process (no workers=) so RunningEdgeState stays in-memory
    # and the 1GB VPS stays light. Never pass workers>1 without rethinking that.
    uvicorn.run(
        "monitor.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
