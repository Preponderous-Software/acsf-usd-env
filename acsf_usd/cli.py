"""Command-line entry point: ``acsf-usd``."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from acsf_usd import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acsf-usd",
        description="Export ACSF runs to OpenUSD stages (read-only against ACSF).",
    )
    parser.add_argument("--version", action="version", version=f"acsf-usd {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
