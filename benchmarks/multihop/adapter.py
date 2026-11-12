"""Separate official sources, inference questions and scoring-only references."""
import argparse
import hashlib
import json
import urllib.request

from benchmarks.common.paths import DATASETS_ROOT, safe_tag
from benchmarks.multihop import protocol as p


def document(title, text):
    text = "".join(text) if isinstance(text, list) else str(text)
    identifier = hashlib.sha256((title + "\0" + text).encode()).hexdigest()[:20]
    return {"id": "d" + identifier, "title": title, "text": text}


def candidate_documents(row, corpus=None):
    if "paragraphs" in row:
        return [document(x["title"], x["paragraph_text"]) for x in row["paragraphs"]]
    docs = []
    for title, sentences in row["context"]:
        doc = document(title, sentences)
        if corpus is not None and doc["id"] not in corpus and isinstance(sentences, list):
            # Some releases store sentence-boundary spaces inside each string;
            # others store just sentences. Resolve this representation against
            # the authoritative corpus, never by dataset name, QA or labels.
            spaced = document(title, " ".join(sentences))
            if spaced["id"] in corpus:
                doc = corpus[spaced["id"]]
        docs.append(doc)
    return docs


def references(row):
    if "paragraphs" in row:
        supports = [x["title"] for x in row["paragraphs"] if x.get("is_supporting")]
    else:
        supports = sorted({x[0] for x in row.get("supporting_facts", [])})
    return {"id": str(row.get("id", row.get("_id"))),
            "answers": [row["answer"], *row.get("answer_aliases", [])],
            "support_titles": supports}


def iter_units(documents):
    for doc in documents:
        yield {"id": doc["id"], "title": doc["title"], "date": "2000-01-01",
               "weekday": "Saturday", "time": "", "t": "", "bucket": "",
               "dur": "", "speakers": ["source"],
               "utterances": [(doc["id"] + "L1", "source", "", doc["title"] + "\n" + doc["text"])]}


def prepare(dataset, tag, smoke_questions=0):
    raw = DATASETS_ROOT / "multihop"
    for name in [f"{dataset}.json", f"{dataset}_corpus.json"]:
        path = raw / name
        if not path.exists():
            raw.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f"https://raw.githubusercontent.com/juevn/Hi-Q/{p.REVISION}/dataset/{name}", path)
        if p.sha(path) != p.SHA256[name]:
            raise ValueError("upstream dataset digest mismatch: " + name)
    questions = p.read(raw / f"{dataset}.json")
    full_docs = [document(x["title"], x["text"]) for x in p.read(raw / f"{dataset}_corpus.json")]
    corpus = {d["id"]: d for d in full_docs}
    reconciled = 0
    if smoke_questions:
        questions = questions[:smoke_questions]
        docs = []
        for q in questions:
            original = candidate_documents(q)
            canonical = candidate_documents(q, corpus)
            reconciled += sum(a["id"] != b["id"] for a, b in zip(original, canonical))
            docs.extend(canonical)
        scope = "SMOKE ONLY: union of every selected question's candidate documents, including distractors; not full-corpus benchmark"
    else:
        docs = full_docs
        scope = "Official Hi-Q shared corpus; every question searches the entire collection"
    docs = list({d["id"]: d for d in docs}.values())
    target, _, _, _ = p.paths(tag, dataset)
    if target.exists():
        raise FileExistsError("prepared data exists; use a new tag")
    target.mkdir(parents=True)
    for name, rows in [("corpus.jsonl", docs),
                       ("questions.jsonl", [{"id": str(q.get("id", q.get("_id"))), "question": q["question"]} for q in questions]),
                       ("references.jsonl", [references(q) for q in questions])]:
        for row in rows:
            p.append(target / name, row)
    p.dump(target / "manifest.json", {"upstream": p.UPSTREAM, "revision": p.REVISION,
           "scope": scope, "exposed_development_data": True, "documents": len(docs),
           "questions": len(questions), "selection": "first N official rows; never filter by gold/support flags",
           "sentence_boundary_reconciliations": reconciled,
           "candidate_documents_outside_official_corpus": sum(d["id"] not in corpus for d in docs),
           "sha256": {name: p.sha(target / name) for name in ["corpus.jsonl", "questions.jsonl", "references.jsonl"]}})
    return {"dataset": dataset, "documents": len(docs), "questions": len(questions), "scope": scope}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="full", type=safe_tag)
    parser.add_argument("--smoke-questions", type=int, default=0)
    args = parser.parse_args()
    if args.smoke_questions < 0:
        parser.error("smoke-questions must be nonnegative")
    for ds in p.DATASETS:
        print(json.dumps(prepare(ds, args.tag, args.smoke_questions)), flush=True)


if __name__ == "__main__":
    main()
