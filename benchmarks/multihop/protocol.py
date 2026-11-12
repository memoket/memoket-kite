"""Pinned data, run metadata and deterministic EM/F1. No LLM judge."""
import hashlib
import json
import os
import re
import string
from collections import Counter
from pathlib import Path

from benchmarks.common.paths import (
    CACHE_ROOT,
    CODEBOOKS_ROOT,
    DATASETS_ROOT,
    REPO_ROOT,
    RESULTS_ROOT,
    safe_tag,
)

DATASETS = ("musique", "hotpotqa", "2wikimultihopqa")
UPSTREAM = "https://github.com/juevn/Hi-Q"
REVISION = "f59729cff8432f93d83a3543346f7f8a1448ef0b"
SHA256 = {
    "musique.json": "98ed4e21d3076532f6388d42320fb809599c63a0d8dffca8ece5e41922be6b46",
    "musique_corpus.json": "73157a03ce3f0b1a5673dd5dc12bb970c24976dbffc688af9eecdd758c97ffcb",
    "hotpotqa.json": "3ad9c0bcbf93f41d7004ca6007049c904d2605046b314a2f7ecfb379c64cba6d",
    "hotpotqa_corpus.json": "9333647b922382776cd2cb02893b390d77984df85a91bfa8be411284978aca7d",
    "2wikimultihopqa.json": "895cba294064df0c3302c76847b1fc08d99b5619f7663dfaa3b65cd780f1cac4",
    "2wikimultihopqa_corpus.json": "9d6e352952aafb18dab22bf8195039461321a44a949df902ae83bce134ad238a",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).exists() else []


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def append(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def paths(tag, dataset, run=None):
    tag = safe_tag(tag)
    if dataset not in DATASETS:
        raise ValueError("unknown collection")
    result_root = RESULTS_ROOT / "multihop" / tag
    if run is not None:
        result_root /= safe_tag(run)
    return (DATASETS_ROOT / "multihop" / tag / dataset,
            CODEBOOKS_ROOT / "multihop" / tag / f"{dataset}.xml",
            result_root / dataset,
            CACHE_ROOT / "multihop" / tag / dataset)


def configure(env_file=None):
    if env_file:
        values = {}
        for line in Path(env_file).read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        for key, alias in [("OPENAI_API_KEY", "CODEBOOK_OPENAI_KEY"),
                           ("OPENAI_BASE_URL", "CODEBOOK_OPENAI_BASE")]:
            value = values.get(key) or values.get(alias)
            if value and not os.environ.get(key):
                os.environ[key] = value
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
    os.environ["KITE_LLM_RETRIES"] = "0"


def source_hashes():
    files = list(Path(__file__).parent.glob("*.py")) + list((REPO_ROOT / "src").rglob("*.py"))
    return {str(path.relative_to(REPO_ROOT)): sha(path) for path in sorted(files)}


def usage(path):
    rows = records(path)
    requests = [r for r in rows if r["event"] == "request"]
    responses = [r for r in rows if r["event"] == "response"]
    return {"HTTP_calls": len(requests), "responses": len(responses),
            "unknown_usage": len(requests) - len(responses),
            "models": dict(Counter(r["response"].get("model", "unknown") for r in responses)),
            "tokens": {key: sum(r["response"].get("usage", {}).get(key, 0) for r in responses)
                       for key in ["prompt_tokens", "completion_tokens", "total_tokens"]},
            "reasoning_tokens": sum(r["response"].get("usage", {}).get("completion_tokens_details", {}).get("reasoning_tokens", 0) for r in responses)}


def normalize(value):
    value = str(value).lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", value).split())


def answer_scores(answer, golds):
    pred = normalize(answer)
    result = []
    for gold in golds:
        gold = normalize(gold)
        common = sum((Counter(pred.split()) & Counter(gold.split())).values())
        if pred == gold:
            f1 = 1.0
        elif pred in {"yes", "no", "noanswer"} or gold in {"yes", "no", "noanswer"} or not common:
            f1 = 0.0
        else:
            precision, recall = common / len(pred.split()), common / len(gold.split())
            f1 = 2 * precision * recall / (precision + recall)
        result.append((float(pred == gold), f1))
    return {"em": max(v[0] for v in result), "f1": max(v[1] for v in result)}
