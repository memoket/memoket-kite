"""Build LoCoMo conversations into KITE Codebooks."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from benchmarks.common.build_provenance import build_attributes, reuse_existing
from benchmarks.common.paths import CACHE_ROOT, CODEBOOKS_ROOT, DATASETS_ROOT
from benchmarks.locomo import adapter, profile
from memoket_kite.core.vocab import Vocab
from memoket_kite.pipeline.extract import (
    append_session_to_xml,
    consolidate_topics,
    extract_facts,
)

DATASET = DATASETS_ROOT / "locomo" / "locomo10.json"
OUTPUT_DIR = CODEBOOKS_ROOT / "locomo"


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.+-]+", "-", model.lower()).strip("-")


def _load_samples(indices: list[int]) -> list[tuple[int, dict]]:
    with DATASET.open(encoding="utf-8") as stream:
        data = json.load(stream)
    try:
        return [(index, data[index]) for index in indices]
    except IndexError as exc:
        raise SystemExit(f"sample index is outside the dataset: {exc}") from exc


def build_sample(
    sample: dict,
    *,
    model: str,
    resume: bool = False,
    force: bool = False,
) -> str:
    """Build one sample atomically and return its sample ID."""
    sample_id = sample["sample_id"]
    output = OUTPUT_DIR / f"{sample_id}.xml"
    speakers = " ".join(
        [sample["conversation"].get("speaker_a", ""), sample["conversation"].get("speaker_b", "")]
    )
    units = list(adapter.iter_units(sample))
    attributes = build_attributes(
        model,
        {"id": sample_id, "speakers": speakers, "units": units},
        builder=__file__,
        adapter=adapter.__file__,
        profile=profile.__file__,
    )
    if reuse_existing(output, attributes, resume=resume, force=force):
        return f"{sample_id}: skipped"

    vocabulary = Vocab(admission_k=4, promote_units=3)
    for topic in profile.SEED_ROOTS:
        vocabulary.define_root(topic)
    root = ET.Element("codebook", id=sample_id, speakers=speakers, **attributes)
    timeline = ET.SubElement(root, "timeline")
    cache_dir = CACHE_ROOT / "extraction" / "locomo" / _model_slug(model) / sample_id
    for session in units:
        facts = extract_facts(
            vocabulary,
            profile,
            session,
            model=model,
            cache_dir=str(cache_dir),
        )
        append_session_to_xml(timeline, profile, session, facts)
        vocabulary.promotion_pass()
        if vocabulary.consolidation_pressure(pool_max=40):
            consolidate_topics(vocabulary, [root], model=model)
    consolidate_topics(vocabulary, [root], model=model)
    vocabulary.promotion_pass()
    problems = vocabulary.check()
    if problems:
        raise RuntimeError(f"vocabulary invariants failed: {problems}")
    root.insert(0, vocabulary.to_xml())
    ET.indent(root)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{sample_id}.", suffix=".xml", dir=OUTPUT_DIR
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        ET.ElementTree(root).write(temporary, encoding="unicode", xml_declaration=True)
        ET.parse(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return f"{sample_id}: built"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, nargs="+", default=list(range(10)))
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    for _, sample in _load_samples(args.samples):
        print(
            build_sample(sample, model=args.model, resume=args.resume, force=args.force),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
