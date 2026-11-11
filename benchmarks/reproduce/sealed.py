"""Read a result directory only through its judge seal.

Row count is not integrity. Opening `judged.jsonl` directly and recomputing the
metric from whatever verdicts are in it cannot distinguish an intact artifact
from one with a flipped verdict, two flipped verdicts that leave the aggregate
unchanged, or no `judge_manifest.json` at all. The seal records a digest for
every input and output of the judging round, so consulting it is what separates
those cases.

The release path therefore takes the same route a scorer takes with `--offline`:
verify every digest the seal records, and parse the bytes that verification
returned rather than re-opening the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.common import judging
from benchmarks.locomo.protocol import JUDGE_PROMPT as LOCOMO_PROTOCOL
from benchmarks.longmemeval.protocol import JUDGE_PROMPTS as LONGMEMEVAL_PROTOCOL

# The rubric each benchmark's verdicts were produced under. `protocol_sha` is
# taken over exactly this text, so a reworded rubric stops the release.
PROTOCOLS = {
    "locomo": LOCOMO_PROTOCOL,
    "longmemeval": json.dumps(LONGMEMEVAL_PROTOCOL, sort_keys=True),
}


def _sources(name: str, result_dir: Path):
    """The answer files the seal binds — one for LongMemEval, many for LoCoMo."""
    if name == "locomo":
        return sorted(result_dir.glob("results_*.jsonl"))
    return result_dir / "results.jsonl"


def verified_records(name: str, judged_path: Path, *, allow_legacy: bool = False) -> list[dict]:
    """Every judged row, or a refusal naming what failed to verify.

    `allow_legacy` accepts an artifact whose seal predates framed source
    digests. It weakens the source binding and nothing else: every other digest
    is still checked, and a release that passes this flag is stating that
    weaker binding rather than hiding it.
    """
    records, _seal = verified_run(name, judged_path, allow_legacy=allow_legacy)
    return records


def verified_run(
    name: str, judged_path: Path, *, allow_legacy: bool = False
) -> tuple[list[dict], dict]:
    """Return checked verdicts with the seal used to verify them."""
    result_dir = judged_path.parent
    recorded = judging.read_manifest(result_dir)
    _model, weak, payload = judging.check_offline(
        result_dir,
        recorded["judge_model"] if recorded else "",
        source=_sources(name, result_dir),
        judged=judged_path,
        protocol=PROTOCOLS[name],
        run=result_dir / "manifest.json",
        score=result_dir / "score.json",
        allow_legacy=allow_legacy,
    )
    if weak and not allow_legacy:
        raise RuntimeError(
            f"{name}: {result_dir.name} carries only a legacy source binding; "
            f"re-judge it before release"
        )
    if recorded is None or judging.read_manifest(result_dir) != recorded:
        raise RuntimeError(f"{result_dir.name}: judge manifest changed during verification; retry")
    records = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    return records, _seal_fields(recorded)


def require_same_corpus(name: str, result_dir: Path, *, dataset: Path, codebooks: Path) -> str:
    """The corpus on disk must be the one the sealed run declares it read.

    Counting `*.xml` files says nothing about their contents: replacing a
    codebook with a different one of the same name leaves both the file count
    and the dataset digests intact, so only a digest taken over the corpus
    itself detects the substitution.
    """
    from benchmarks.common import manifest

    recorded = json.loads((result_dir / "manifest.json").read_text()).get("corpus_sha")
    if not recorded:
        raise RuntimeError(
            f"{name}: {result_dir.name} declares no corpus_sha, so what it read cannot "
            f"be verified; re-run it before release"
        )
    current = manifest.corpus_digest([dataset, codebooks])
    if current != recorded:
        raise RuntimeError(
            f"{name}: sealed against corpus {recorded}, but the corpus on disk is "
            f"{current}; restore it before packaging"
        )
    return recorded


def seal(result_dir: Path) -> dict:
    """What the artifact says about who judged it, for the release record."""
    recorded = judging.read_manifest(result_dir)
    if recorded is None:
        raise RuntimeError(f"{result_dir.name} has no judge manifest; it cannot be released")
    return _seal_fields(recorded)


def _seal_fields(recorded: dict) -> dict:
    return {
        key: recorded.get(key)
        for key in (
            "schema",
            "judge_model",
            "provider",
            "protocol_sha",
            "results_sha",
            "judged_sha",
            "run_manifest_sha",
            "score_sha",
        )
    }
