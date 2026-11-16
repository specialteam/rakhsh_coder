"""Command line entry point: ``dastyar serve``."""

from __future__ import annotations

import argparse
import os
import secrets
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dastyar", description="Personal agent (web UI)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="start the web server")
    serve.add_argument("--host", help="override config host (non-loopback needs DASTYAR_TOKEN)")
    serve.add_argument("--port", type=int, help="override config port")
    serve.add_argument("--fake", action="store_true", help="use the fake model (no API keys)")
    sub.add_parser("init", help="create ~/.dastyar with a default config and .env template")
    sub.add_parser("token", help="print a new random access token")
    args = parser.parse_args(argv)

    if args.cmd == "token":
        print(secrets.token_urlsafe(32))
        return 0

    from . import paths
    from .config import load_settings

    if args.cmd == "init":
        settings = load_settings()
        env = paths.env_path()
        if not env.exists():
            env.write_text(
                "# API keys (never commit this file)\n"
                "ANTHROPIC_API_KEY=\nOPENROUTER_API_KEY=\nOPENAI_API_KEY=\n"
                "# Required when the server listens on a non-local address:\n"
                "# DASTYAR_TOKEN=\n# HTTPS_PROXY=\n",
                encoding="utf-8",
            )
        print(f"Data directory: {paths.home()}\nConfig: {paths.config_path()}\nKeys:   {env}")
        print(f"Default model: {settings.default_model}")
        return 0

    if args.fake:
        os.environ["DASTYAR_FAKE_MODEL"] = "1"

    import uvicorn

    from .context import AppContext
    from .server.app import ConfigError, create_app

    settings = load_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    try:
        app = create_app(AppContext.build(settings))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    shown = "localhost" if settings.host in ("127.0.0.1", "::1") else settings.host
    print(f"Dastyar is running: http://{shown}:{settings.port}")
    if os.environ.get("DASTYAR_FAKE_MODEL") == "1":
        print("(fake model: no API keys needed, try '!sh echo hi')")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
