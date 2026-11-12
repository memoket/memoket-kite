"""Read labels only after every prediction freezes; report failures as zero."""
import argparse
import json

from benchmarks.common.paths import safe_tag
from benchmarks.multihop import protocol as p
from memoket_kite.core.algebra import Store


def evidence_units(result, store):
    """Native pack_rows retain IDs/types/src, so resolve units through Store."""
    ids = [r.get("id") for r in result.get("pack_rows", [])] + result.get("pack_src", [])
    return {record.unit for identifier in ids
            if (record := store.facts.get(identifier) or store.lines.get(identifier)) is not None}


def score(dataset, tag, run=None):
    data, memory, output, _ = p.paths(tag, dataset, run)
    freeze = p.read(output / "freeze.json")
    assert freeze["complete"] and p.sha(output / "predictions.jsonl") == freeze["predictions_sha256"]
    refs = {r["id"]: r for r in p.records(data / "references.jsonl")}
    titles = {r["id"]: r["title"] for r in p.records(data / "corpus.jsonl")}
    store, _ = Store.load([str(memory)])
    build = p.read(memory.with_suffix(".manifest.json"))
    cases = []
    for row in p.records(output / "predictions.jsonl"):
        ref = refs[row["id"]]
        values = p.answer_scores(row["answer"], ref["answers"]) if row["status"] == "ok" else {"em": 0.0, "f1": 0.0}
        result = row.get("result", {})
        plan = result.get("plan", {})
        units = evidence_units(result, store)
        found = {titles[u] for u in units if u in titles}
        cases.append({"id": row["id"], "question": row["question"], "answer": row["answer"],
                      "gold": ref["answers"], "status": row["status"], **values,
                      "staged_plan": bool(plan.get("stages")), "evidence_documents": len(units),
                      "all_support_titles_retrieved": set(ref["support_titles"]) <= found})
    report = {"dataset": dataset, "n": len(cases),
              "EM": sum(c["em"] for c in cases) / len(cases),
              "F1": sum(c["f1"] for c in cases) / len(cases),
              "errors": sum(c["status"] != "ok" for c in cases),
              "staged_plan_rate": sum(c["staged_plan"] for c in cases) / len(cases),
              "stages_are_not_proof_of_semantic_multihop": True,
              "extraction_complete": build["extraction_complete"],
              "failed_extractions": len(build["failed_extractions"]),
              "scoring_source_sha256": p.sha(__file__),
              "LLM_judge": False, "scope": p.read(data / "manifest.json")["scope"], "cases": cases}
    p.dump(output / "scores.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="full", type=safe_tag)
    parser.add_argument("--run", type=safe_tag)
    args = parser.parse_args()
    # Global freeze gate, not a score-as-you-go loop.
    for ds in p.DATASETS:
        _, _, output, _ = p.paths(args.tag, ds, args.run)
        if not p.read(output / "freeze.json")["complete"]:
            raise RuntimeError("all three collections must freeze before scoring")
    for ds in p.DATASETS:
        print(json.dumps(score(ds, args.tag, args.run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
