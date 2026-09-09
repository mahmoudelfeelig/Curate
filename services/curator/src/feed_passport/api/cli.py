from __future__ import annotations

import argparse
import ipaddress
import os

import uvicorn


def _is_loopback_host(value: str) -> bool:
    normalized = value.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Feed Passport curator API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    auth_mode = os.getenv("FEED_PASSPORT_AUTH_MODE", "demo").strip().lower()
    if auth_mode == "demo" and not _is_loopback_host(args.host):
        parser.error("demo authentication requires a loopback --host; use OIDC beyond this machine")
    if (
        os.getenv("FEED_PASSPORT_ENABLE_LOCAL_IMPORT", "0").strip() == "1"
        and not _is_loopback_host(args.host)
    ):
        parser.error("local Instagram import requires a loopback --host")
    # The app's loopback-only local-import guard must evaluate the host that
    # Uvicorn will actually bind, not a separate stale environment default.
    os.environ["FEED_PASSPORT_BIND_HOST"] = args.host
    uvicorn.run(
        "feed_passport.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
