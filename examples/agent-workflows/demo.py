"""Exercise the synthetic fixture from a source checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoket_kite.tools.demo import run_demo

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", action="store_true", help="emit deterministic fixture metrics")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    print(json.dumps(run_demo(repo, metrics=args.metrics), indent=2))
