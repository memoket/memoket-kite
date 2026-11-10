# Development

## Setup and checks

```bash
python -m pip install -e ".[dev,benchmark,mcp,agent]"
ruff check src tests benchmarks examples
ruff format --check src tests benchmarks examples
pytest --cov=memoket_kite --cov-fail-under=75
python -m build
```

Offline tests and examples do not require provider credentials. For LLM-backed
examples or benchmarks, copy `.env.example`, fill it, and explicitly `source`
the file in the current shell.

## Repository boundaries

- Runtime behavior belongs in `src/memoket_kite`.
- Dataset adapters, frozen profiles, and scoring protocols belong in
  `benchmarks/<name>`.
- Generated or downloaded content belongs in the ignored `artifacts/` tree.
- Prompts used to report benchmark results are part of the protocol: review and
  document any change to them.
- Do not add `sys.path` mutation, import-time environment changes, file scans,
  network calls, or output.
- Keep mechanical cleanup separate from algorithm or benchmark changes.

## Release checks

The wheel must contain only `memoket_kite` plus standard distribution metadata.
Benchmark modules must import without starting work, and all six benchmark
entry points must support `--help` without datasets or credentials. See
[`reproducibility.md`](reproducibility.md) for local benchmark reproduction and
validation.
