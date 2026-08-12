#!/usr/bin/env python3
"""Create a small deterministic MS text source for Phase 2 mouse UAT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ms_event_studio.demo import create_guided_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(create_guided_source(args.output), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
