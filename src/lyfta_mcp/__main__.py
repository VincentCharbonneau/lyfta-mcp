"""Self-hosted Lyfta MCP server: `lyfta-mcp [--host H] [--port P]`.

Serves Streamable HTTP at /mcp (requires the MCP_AUTH_TOKEN bearer token) and GET /health.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .auth import BearerTokenMiddleware
from .client import LyftaClient
from .config import ConfigError, Settings, load_dotenv, load_settings
from .server import create_server


def build_app(
    settings: Settings, host: str = "0.0.0.0", client: LyftaClient | None = None
) -> BearerTokenMiddleware:
    """Streamable HTTP app at /mcp behind the access-token check, plus GET /health."""
    mcp = create_server(settings, client)
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=host)
    return BearerTokenMiddleware(app, token=settings.auth_token)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lyfta-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="interface to bind (default 127.0.0.1; the Docker image uses 0.0.0.0)",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"lyfta-mcp: configuration error\n{exc}", file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(
        stream=sys.stderr,
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    import uvicorn

    uvicorn.run(
        build_app(settings, host=args.host),
        host=args.host,
        port=args.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
