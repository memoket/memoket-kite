"""Assemble a local Codex marketplace using a built KITE wheel."""

import argparse
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new marketplace directory")
    parser.add_argument("--wheel", type=Path, required=True, help="built KITE wheel")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file() or not re.fullmatch(r"memoket_kite-.+-py3-none-any\.whl", wheel.name):
        parser.error("--wheel must be a built universal memoket-kite wheel")
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        parser.error("choose a new output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="kite-marketplace-", dir=output.parent) as staging:
        market = Path(staging) / "marketplace"
        metadata = market / ".agents" / "plugins"
        metadata.mkdir(parents=True)
        shutil.copy2(ROOT / ".agents" / "plugins" / "marketplace.json", metadata)
        plugin = market / "plugins" / "kite"
        shutil.copytree(
            ROOT / "plugins" / "kite",
            plugin,
            ignore=shutil.ignore_patterns("runtime", "__pycache__"),
        )
        runtime = plugin / "runtime"
        runtime.mkdir()
        shutil.copy2(wheel, runtime)
        market.rename(output)
    print(output)


if __name__ == "__main__":
    main()
