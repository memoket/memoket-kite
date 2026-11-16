"""Record the symbolic result from the workspace MCP process that served Codex."""

import json
import sys
from pathlib import Path


def main():
    from memoket_kite.mcp.server import main as serve
    from memoket_kite.tools import symbolic

    destination = Path(sys.argv.pop(1))
    render = symbolic.render_symbolic_context

    def observed(receipt, *args, **kwargs):
        text = render(receipt, *args, **kwargs)
        with destination.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"receipt": receipt, "text": text}, ensure_ascii=False) + "\n")
        return text

    symbolic.render_symbolic_context = observed
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
