"""Explicit startup configuration; never use a scientific environment or JWT."""

import argparse
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="BP-Reviewer local inspection API")
    parser.add_argument("--allow-root", action="append", required=True, type=Path)
    parser.add_argument("--state", type=Path, default=Path("state"))
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    token = os.environ.get("BPREVIEWER_TOKEN") or secrets.token_urlsafe(32)
    from .api import create_app
    import uvicorn

    print("BP-Reviewer local access token (enter in browser): " + token)
    uvicorn.run(create_app(args.state, args.allow_root, token), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
