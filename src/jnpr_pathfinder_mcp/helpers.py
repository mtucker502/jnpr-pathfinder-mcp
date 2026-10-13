import argparse
from typing import Any, Optional

import requests

EMPTY_RESPONSE_ERROR = "Empty response from API."

def parse_args(prog='jnpr_pathfinder_mcp'):
    parser = argparse.ArgumentParser(
            prog=prog,
            description="An MCP Server for interacting with Juniper's Pathfinder Apps.")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio", help="transport to use"
    )
    parser.add_argument("--host", help="host for http transport", default=None)
    parser.add_argument("--port", help="port for http transport", type=int, default=None)
    return parser.parse_args()


def run_cli(prog, server):
    args = parse_args(prog)
    kwargs = {'transport': args.transport}

    if args.transport == "stdio":
        if (args.host is not None or args.port is not None):
            raise ValueError("host/port cannot be used with stdio transport")
    elif args.transport == "http":
        kwargs['host'] = args.host
        kwargs['port'] = args.port
    else:
        raise ValueError(f"transport must be 'stdio' or 'http'")

    server.run(**kwargs)


def post_json(
    url: str,
    payload: Optional[dict[str, Any]] = None,
    empty_error: str = EMPTY_RESPONSE_ERROR,
    verify: bool = True,
    timeout: float = 60,
) -> tuple[Any, Optional[str]]:
    """POST to a Pathfinder API and unwrap the {"status": ..., "data": ...} envelope.

    The Pathfinder services report most errors with HTTP 200 and status.success=false,
    so both the HTTP status and the envelope are checked.

    Returns (data, error); exactly one of them is set.
    """
    response = requests.post(url, json=payload or {}, verify=verify, timeout=timeout)
    if not (response.ok and len(response.content)):
        return None, response.text or empty_error
    body = response.json()
    if isinstance(body, dict) and "status" in body:
        status = body.get("status") or {}
        if not status.get("success"):
            return None, status.get("errorMessage") or empty_error
        body = body.get("data")
    if not body:
        return None, empty_error
    return body, None
