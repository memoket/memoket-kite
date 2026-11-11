"""Exercise real benchmark subprocesses with a synthetic loopback model."""

import json
import os
import re
import shutil
import subprocess
import sys
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "tests" / "fixtures" / "corpus" / "datasets"


def _model_response(benchmark, model, prompt):
    if model == "smoke-build":
        line = re.search(r"(?m)^([^|\n]+)\|[^\n]*\|I planted basil in the garden today\.", prompt)
        if not line:
            return {"assignments": []}
        return {
            "facts": [
                {
                    "content": "I planted basil in the garden on May 1, 2023.",
                    "kind": "event",
                    "topics": ["general"],
                    "src": [line[1]],
                }
            ]
        }
    if model == "smoke-plan":
        return {"queries": [{"select": "facts", "where": {}, "pipe": []}], "intent": "factual"}
    if model in ("smoke-answer", "smoke-candidate"):
        answer = "Synthetic wrong answer" if model == "smoke-candidate" else "Basil"
        if "When did Ada plant it?" in prompt:
            answer = "1 May 2023"
        elif "What should I plant next?" in prompt:
            answer = "More basil"
        elif "What did I name my boat?" in prompt:
            answer = "I don't know."
        return {"answer": answer, "evidence": []}
    if model == "smoke-judge":
        wrong = "Synthetic wrong answer" in prompt
        return (
            {"label": "WRONG" if wrong else "CORRECT", "reasoning": "Synthetic fixture."}
            if benchmark == "locomo"
            else ("no" if wrong else "yes")
        )
    raise ValueError(f"unexpected synthetic model {model}")


@pytest.mark.parametrize(
    ("benchmark", "selection", "answers"),
    [
        ("locomo", ("--samples", "0"), ["Basil", "1 May 2023"]),
        ("longmemeval", ("--n", "0"), ["Basil", "More basil", "I don't know."]),
    ],
)
def test_benchmark_reproduction_with_a_loopback_model(tmp_path, benchmark, selection, answers):
    artifacts = tmp_path / "artifacts"
    shutil.copytree(DATASETS, artifacts / "datasets")
    result = artifacts / "results" / f"{benchmark}-smoke"
    judged = result / "judged.jsonl"
    # Keep untracked scratch files from the developer's checkout out of this run.
    # Git still records tracked edits, and the repository's guard has its own tests.
    excludes = tmp_path / "git-excludes"
    excludes.write_text("*\n", encoding="utf-8")
    requests = Queue()

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = payload["model"]
            requests.put(model)
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer synthetic-loopback-key"
            result = _model_response(benchmark, model, payload["messages"][0]["content"])
            content = json.dumps(result) if isinstance(result, dict) else result
            response = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Endpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever, daemon=True)
        worker.start()
        environment = {
            **os.environ,
            "KITE_ARTIFACTS_DIR": str(artifacts),
            "OPENAI_API_KEY": "synthetic-loopback-key",
            "OPENAI_BASE_URL": f"http://127.0.0.1:{endpoint.server_port}/v1",
            "no_proxy": "127.0.0.1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.excludesFile",
            "GIT_CONFIG_VALUE_0": str(excludes),
        }

        def run_module(module, *args, error=None):
            process = subprocess.run(
                [sys.executable, "-m", module, *args],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
            output = process.stdout + process.stderr
            if error is None:
                assert process.returncode == 0, output
            else:
                assert process.returncode != 0 and error in output, output
            return process.stdout

        def run(stage, *args, error=None):
            return run_module(f"benchmarks.{benchmark}.{stage}", *args, error=error)

        try:
            build = (*selection, "--model", "smoke-build")
            evaluate = (
                *selection,
                "--workers",
                "1",
                "--model",
                "smoke-plan",
                "--answer-model",
                "smoke-answer",
                "--tag",
                "smoke",
            )
            judge = ("--workers", "1", "--judge-model", "smoke-judge", "--tag", "smoke")
            run("build", *build)
            run("evaluate", *evaluate)
            count = requests.qsize()
            run("build", *build, "--resume")
            run("evaluate", *evaluate, "--resume")
            assert requests.qsize() == count
            run("score", *judge)
            verification = artifacts / "verification.json"
            codebooks = artifacts / "codebooks" / benchmark
            dataset = next((artifacts / "datasets" / benchmark).glob("*.json"))
            verification.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "datasets": {
                            benchmark: {
                                "path": f"artifacts/{dataset.relative_to(artifacts).as_posix()}",
                                "sha256": sha256(dataset.read_bytes()).hexdigest(),
                            }
                        },
                        "benchmarks": {
                            benchmark: {
                                "codebook_path": f"artifacts/codebooks/{benchmark}",
                                "result_path": f"artifacts/results/{result.name}/judged.jsonl",
                                "codebooks": 1 if benchmark == "locomo" else len(answers),
                                "result_rows": len(answers),
                                "metric": "accuracy",
                                "score": 1.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            verify = ("benchmarks.reproduce.verify", benchmark, "--manifest", str(verification))
            count = requests.qsize()
            assert "accuracy=1.000000" in run_module(*verify)
            codebook = next(codebooks.glob("*.xml"))
            original_book = codebook.read_bytes()
            codebook.write_bytes(original_book + b"\n<!-- changed corpus -->\n")
            run_module(*verify, error="corpus on disk is")
            codebook.write_bytes(original_book)
            assert requests.qsize() == count
            run("evaluate", *evaluate, "--answer-model", "smoke-candidate", "--tag", "candidate")
            run("score", *judge, "--tag", "candidate")
            candidate = result.with_name(f"{benchmark}-candidate")
            count = requests.qsize()
            run("score", *judge, "--offline")
            report = json.loads(
                run_module("benchmarks.reproduce.compare", benchmark, str(result), str(candidate))
            )
            assert report["overall"]["n"] == len(answers)
            assert report["overall"]["regressed"] == 1
            assert report["overall"]["improved"] == 0
            assert report["overall"]["unchanged"] == len(answers) - 1
            assert report["overall"]["metrics"]["accuracy"] == pytest.approx(
                {"before": 1.0, "after": 1.0 - 1 / len(answers), "delta": -1 / len(answers)}
            )
            assert len(report["changed"]) == 1
            changed = report["changed"][0]
            assert (changed["before"], changed["after"]) == (1, 0)
            assert report["groups"][changed["group"]]["regressed"] == 1
            assert report["baseline"]["results_sha"] != report["candidate"]["results_sha"]

            judge_manifest = candidate / "judge_manifest.json"
            original = judge_manifest.read_text(encoding="utf-8")
            metadata = json.loads(original)
            metadata["provider"] = "https://different-provider.example/v1"
            judge_manifest.write_text(json.dumps(metadata), encoding="utf-8")
            run_module(
                "benchmarks.reproduce.compare",
                benchmark,
                str(result),
                str(candidate),
                error="provider differs or is missing",
            )
            judge_manifest.write_text(original, encoding="utf-8")
            sealed_verdicts = judged.read_bytes()
            judged.write_bytes(sealed_verdicts + b"\n")
            run("score", *judge, "--offline", error="judged_sha changed since judging")
            run_module(
                "benchmarks.reproduce.compare",
                benchmark,
                str(result),
                str(candidate),
                error="judged_sha changed since judging",
            )
            assert requests.qsize() == count
        finally:
            endpoint.shutdown()
            worker.join(timeout=5)

    models = [requests.get_nowait() for _ in range(requests.qsize())]
    assert {"smoke-build", "smoke-plan", "smoke-answer", "smoke-judge"} <= set(models)
    assert models.count("smoke-judge") == 2 * len(answers)
    assert [json.loads(line)["answer"] for line in sealed_verdicts.splitlines()] == answers
    score = json.loads((result / "score.json").read_text(encoding="utf-8"))
    assert score["overall"]["n"] == len(answers)
    assert score["overall"]["accuracy"] == 1.0
    assert score["overall"]["evidence_recall"] == 1.0
    assert score["provenance"] == {"source_binding": "verified", "corpus_binding": "verified"}
    assert (result / "judge_manifest.json").is_file()


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"question_id": "another-id"}, "question sets differ"),
        ({"question_id": "control"}, "duplicate question ID"),
        ({"gold": "a different answer"}, "different gold"),
        ({"answer_session_ids": []}, "different answer_session_ids"),
        ({"ok": 0.5}, "non-binary verdict"),
    ],
)
def test_comparison_requires_the_same_questions_and_valid_verdicts(monkeypatch, update, message):
    from benchmarks.reproduce import compare, sealed

    question = {
        "question_id": "original",
        "question": "What did I plant?",
        "question_type": "single-session-user",
        "gold": "Basil",
        "answer_session_ids": ["session-1"],
        "ok": 1,
    }
    control = {**question, "question_id": "control"}
    judge = {field: "synthetic" for field in compare.JUDGE_FIELDS}
    runs = {
        "baseline": [control, question],
        "candidate": [control, {**question, **update}],
    }
    monkeypatch.setattr(sealed, "verified_run", lambda _, path: (runs[path.parent.name], judge))
    with pytest.raises(ValueError, match=message):
        compare.compare("longmemeval", Path("baseline"), Path("candidate"))
