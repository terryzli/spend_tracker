#!/usr/bin/env python3
"""Performs a fresh OAuth authentication flow and generates token.json."""

import argparse
import os
import sys
from typing import List, Optional

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from pkg.common import ROOT_DIR, get_config_path, get_credentials, load_config


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Google OAuth authentication and regenerate token.json.")
    parser.add_argument("--port", type=int, default=8080, help="Local callback server port (default: 8080).")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL instead of launching a browser (useful for SSH/headless hosts).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(root_dir=ROOT_DIR)
    token_path = get_config_path(config, "token", "token.json", root_dir=ROOT_DIR)

    if os.path.exists(token_path):
        os.remove(token_path)
        print(f"Removed existing {token_path} for a fresh authentication test.")

    try:
        get_credentials(config, root_dir=ROOT_DIR, port=args.port, open_browser=not args.no_browser)
        print(f"\nSuccessfully authenticated and created {token_path}!")
        return 0
    except Exception as exc:
        print(f"\nAn error occurred during authentication: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
