# Contributing to KITE

Thank you for contributing to KITE. Clear behavior, traceable evidence, and
reproducible evaluation take priority over expanding the API surface.

## Before you start

- Search [existing issues](https://github.com/memoket/memoket-kite/issues) before
  opening a new one.
- Use the appropriate form for a
  [bug](https://github.com/memoket/memoket-kite/issues/new?template=bug.yml),
  [feature request](https://github.com/memoket/memoket-kite/issues/new?template=feature.yml),
  or [benchmark discrepancy](https://github.com/memoket/memoket-kite/issues/new?template=reproduction.yml).
- Discuss public API or benchmark protocol changes in an issue before starting
  a large implementation.
- Report security vulnerabilities privately through
  [GitHub Security Advisories](https://github.com/memoket/memoket-kite/security/advisories/new).

## Development setup

```bash
python -m pip install -e ".[dev,benchmark,mcp,agent]"
ruff check src tests benchmarks examples
ruff format --check src tests benchmarks examples
pytest
```

To exercise the installed agent workflows, build the distributions and run the
smoke check from a fresh virtual environment with the `agent` extra installed.

```sh
check_dir="$(mktemp -d)"
python -m build --outdir "$check_dir/dist"
python -m venv "$check_dir/venv"
"$check_dir/venv/bin/python" -m pip install "$(echo "$check_dir"/dist/*.whl)[agent]"
"$check_dir/venv/bin/python" tests/packaging/agent_smoke.py
```

The same check runs against the source distribution in CI and both
distributions before publication.

## Pull requests

- Keep each pull request focused and explain both the change and its motivation.
- Keep algorithm changes separate from mechanical cleanup and documentation.
- Add deterministic regression coverage for retrieval, trace, serialization,
  and public API changes.
- Update user-facing documentation when behavior or public APIs change.
- Do not introduce import-time file scans, environment mutations, network
  calls, or output.
- Keep all generated datasets, Codebooks, caches, results, and reports under
  `artifacts/`; never commit credentials or private absolute paths.
- Use the existing public `Memory.load/remember/recall/answer` contract unless
  an API proposal has been discussed first.

## Benchmark and data changes

Benchmark claims must identify the exact dataset revision and SHA256, model IDs,
prompt/protocol changes, denominator, and local generated-artifact hashes.
Update the reproduction manifest and add an offline metric test. Do not compare
scores from different dataset revisions as if they were the same experiment.

Before adding any corpus or fixture, confirm that its license permits
redistribution. Official benchmark datasets and generated benchmark outputs
remain local under `artifacts/`; do not commit or upload them.

## Security and privacy

Remove private conversational content from bug reports and fixtures. Report
vulnerabilities through the process in
[`SECURITY.md`](https://github.com/memoket/memoket-kite/blob/main/SECURITY.md).
