"""CLI entrypoint: `lyfta-mcp [--transport stdio|http] [--host H] [--port P]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .auth import BearerKeyMiddleware, configure_stdio
from .client import LyftaClient
from .config import Settings, load_settings
from .server import create_server


def build_http_app(
    settings: Settings, host: str = "0.0.0.0", client: LyftaClient | None = None
) -> BearerKeyMiddleware:
    """Streamable HTTP app at /mcp (stateless, multi-user) plus GET /health."""
    mcp = create_server(settings, client)
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=host)
    return BearerKeyMiddleware(app, allowlist_sha256=settings.allowlist_sha256)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lyfta-mcp", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    settings = load_settings()
    # stdout carries the stdio protocol, so all logging goes to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.transport == "stdio":
        key = os.environ.get("LYFTA_API_KEY", "").strip()
        if not key:
            print(
                "lyfta-mcp: LYFTA_API_KEY is not set. Generate a key at "
                "https://my.lyfta.app/community/api and pass it in the environment.",
                file=sys.stderr,
            )
            sys.exit(2)
        configure_stdio(key)
        create_server(settings).run("stdio")
        return

    import uvicorn

    port = args.port
    uvicorn.run(
        build_http_app(settings, host=args.host),
        host=args.host,
        port=port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
