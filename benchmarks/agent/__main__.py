"""Run or score KITE's synthetic agent task benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

FIXTURE = Path(__file__).with_name("fixture.json")
RECOVERY_FIXTURE = Path(__file__).with_name("recovery.json")
RECOVERY_CASE = "memoket-resume"


class BenchmarkError(RuntimeError):
    pass


class Kite:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.calls = 0
        self.successes = 0

    def call(self, *args: str, payload: dict | None = None, error: str | None = None) -> dict:
        command = [
            sys.executable,
            "-m",
            "memoket_kite.tools.cli",
            "--workspace",
            str(self.workspace),
            *args,
        ]
        if payload is not None:
            command.extend(("--input", "-"))
        process = subprocess.run(
            command,
            input=None if payload is None else json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        self.calls += 1
        try:
            result = json.loads(process.stdout)
        except ValueError as exc:
            raise BenchmarkError(f"KITE {args[0]} produced no JSON response") from exc
        if process.returncode == 0 and (
            result.get("ok") is True or (args[0] == "file-source" and "source" in result)
        ):
            self.successes += 1
            if error is None:
                return result
        if error and process.returncode != 0 and result.get("error", {}).get("code") == error:
            return result
        code = result.get("error", {}).get("code", "unexpected_success")
        raise BenchmarkError(f"KITE {args[0]} returned {code}")

    def start(self, pipeline: str, run: str, payload: dict) -> None:
        self.call("start", pipeline, "--run-id", run, payload=payload)

    def stage(self, run: str, stage: str, **options) -> dict:
        return self.call("stage", run, stage, **options)

    def trace(self, run: str, case: dict) -> dict:
        self.start(case["pipeline"], run, {"query": case["query"]})
        self.stage(run, "resolve")
        self.stage(run, "retrieve")
        return self.stage(run, "report")["value"]


def _ingest(kite: Kite, run: str, source: dict, *, update=False, interrupt=False) -> dict:
    payload = {"source": source["source"]} if interrupt else source
    kite.start("memory-update" if update else "memory-ingestion", run, payload)
    conversation = kite.stage(run, "normalize")["value"]["conversation"]
    if interrupt:
        kite.stage(run, "annotate", error="missing_input")
        kite.stage(run, "annotate", payload={"annotations": source["annotations"]})
    else:
        kite.stage(run, "annotate")
    receipt = kite.stage(run, "commit")["value"]
    checkpoint = kite.stage(run, "verify")["checkpoint"]
    return {
        "changed": receipt["changed"],
        "completed": checkpoint["state"] == "completed",
        "retries": sum(stage["retry_count"] for stage in checkpoint["stages"].values()),
        "source_bytes": sum(len(line["text"].encode()) for line in conversation["segments"]),
    }


def _seed(kite: Kite, fixture: dict) -> list[dict]:
    first, correction, *remaining = deepcopy(fixture["sources"])
    results = [_ingest(kite, "seed-0", first, interrupt=True)]
    prior = kite.trace("seed-owner", fixture["cases"]["owner-handoff"])
    owners = [row for row in prior["timeline"] if row["state_key"] == "export-owner"]
    if len(owners) != 1:
        raise BenchmarkError("fixture did not establish an initial owner")
    for fact in correction["annotations"]["facts"]:
        if fact.get("state_key") == "export-owner":
            fact["supersedes"] = owners[0]["fact_id"]
    results.append(_ingest(kite, "seed-1", correction, update=True))
    results.extend(_ingest(kite, f"seed-{i}", source) for i, source in enumerate(remaining, 2))
    return results


def _citation(value: dict) -> tuple[str, str]:
    return value["conversation_id"], value["segment_id"]


def _claim(value, rows: list[dict]) -> dict:
    citations = dict.fromkeys(_citation(line) for row in rows for line in row["evidence"])
    return {
        "value": value,
        "citations": [{"conversation_id": a, "segment_id": b} for a, b in citations],
    }


def _reference_answer(case: dict, run: str, report: dict) -> dict:
    claims = {}
    for key, (mode, state_key) in case["select"].items():
        timeline = report["timeline"]
        rows = [row for row in timeline if row["state_key"] == state_key]
        current = report["current_reported_states"].get(state_key, {})
        if mode == "state":
            claims[key] = _claim(
                current.get("value"),
                [r for r in rows if r["fact_id"] in current.get("fact_ids", [])],
            )
        elif mode == "dates":
            by_date = {}
            for row in timeline:
                if row["source_day"]:
                    by_date.setdefault(row["source_day"], row)
            claims[key] = _claim(list(by_date), list(by_date.values()))
        elif mode == "unconfirmed":
            claims[key] = _claim(current.get("value"), [r for r in rows if r["status"] == "open"])
        else:
            candidates = [
                r for r in rows if r["status"] == ("confirmed" if mode == "history" else mode)
            ]
            claims[key] = (
                _claim(candidates[0]["state"], candidates[:1]) if candidates else _claim(None, [])
            )
    return {"run_id": run, "claims": claims}


def score_claims(case: dict, claims: dict, report: dict | None) -> dict:
    lines = {
        _citation(line): line["text"]
        for row in (report or {}).get("timeline", [])
        for line in row["evidence"]
    }
    expected = case["expected"]
    extra = len(claims.keys() - expected.keys())
    supported = 0
    hits = 0
    sent = 0
    required = 0
    cited = set()
    for name, gold in expected.items():
        groups = case.get("allow_citations", {}).get(
            name,
            [[list(_citation(item))] for item in gold["citations"]],
        )
        groups = [{tuple(item) for item in group} for group in groups]
        required += len(groups)
        claim = claims.get(name)
        if not isinstance(claim, dict) or not isinstance(claim.get("citations"), list):
            continue
        try:
            refs = [_citation(item) for item in claim["citations"]]
            actual = set(refs)
        except (KeyError, TypeError):
            continue
        sent += len(refs)
        cited.update(actual & lines.keys())
        valid = actual & lines.keys()
        matched = sum(bool(group & valid) for group in groups)
        hits += matched
        if (
            "value" in claim
            and claim["value"] == gold["value"]
            and matched == len(groups) == len(refs) == len(actual)
        ):
            supported += 1
    return {
        "expected_claims": len(expected),
        "submitted_claims": len(claims),
        "supported_claims": supported,
        "unsupported_claims": len(claims) - supported,
        "expected_citations": required,
        "submitted_citations": sent,
        "expected_citations_found": hits,
        "cited_evidence_bytes": sum(len(lines[key].encode()) for key in cited),
        "passed": supported == len(expected) and not extra,
    }


def _grade(kite: Kite, case: dict, answer: dict, seeded: dict) -> dict:
    run = answer.get("run_id")
    report = None
    completed = False
    attempts = retries = 0
    if isinstance(run, str) and run:
        try:
            status = kite.call("status", run)
            completed = (
                status["pipeline_id"] == case["pipeline"]
                and status["state"] == "completed"
                and status["snapshot_current"]
                and status["memory_version"] == seeded["memory_version"]
            )
            if completed:
                report = kite.call("artifact", run, "report")["value"]
                attempts = sum(stage["attempts"] for stage in status["stages"].values())
                retries = sum(stage["retry_count"] for stage in status["stages"].values())
        except BenchmarkError:
            completed = False
    unchanged = kite.call("integrity")["codebook_sha256"] == seeded["codebook_sha256"]
    claims = answer.get("claims")
    result = score_claims(case, claims if isinstance(claims, dict) else {}, report)
    result.update(
        pipeline_completed=completed,
        workspace_unchanged=unchanged,
        stage_attempts=attempts,
        stage_retries=retries,
        source_corpus_bytes=seeded["source_corpus_bytes"],
    )
    result["passed"] &= completed and unchanged
    return result


def _snapshot(kite: Kite, ingestions: list[dict]) -> dict:
    integrity = kite.call("integrity")
    return {
        "memory_version": integrity["memory_version"],
        "codebook_sha256": integrity["codebook_sha256"],
        "source_corpus_bytes": sum(item["source_bytes"] for item in ingestions),
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
    }


def offline(fixture: dict) -> dict:
    with TemporaryDirectory(prefix="kite-agent-eval-") as directory:
        kite = Kite(Path(directory) / "memory")
        ingestions = _seed(kite, fixture)
        snapshot = _snapshot(kite, ingestions)
        duplicate = _ingest(kite, "duplicate", fixture["sources"][0])
        stable = _snapshot(kite, ingestions) == snapshot
        cases = {}
        for name, case in fixture["cases"].items():
            report = kite.trace(name, case)
            case_result = _grade(kite, case, _reference_answer(case, name, report), snapshot)
            source_ids = {
                line["conversation_id"] for row in report["timeline"] for line in row["evidence"]
            }
            case_result["source_scope_correct"] = source_ids == set(
                case["expected_source_ids"]
            ) and report["omitted_unknown_time"] == case.get("expected_omitted_unknown_time", 0)
            case_result["passed"] &= case_result["source_scope_correct"]
            cases[name] = case_result

        kite.start(
            "memory-deletion",
            "deletion",
            {"target": {"source": "text", "conversation_id": "plan-alpha"}},
        )
        token = kite.stage("deletion", "preview")["value"]["approval_token"]
        kite.call("stage", "deletion", "apply", "--approve", token)
        kite.call("artifact", "format-decision", "report", error="invalidated_run")
        # The deleted conversation contributed the alias, so check surviving
        # evidence with the canonical topic.
        surviving = kite.trace(
            "after-delete",
            {"pipeline": "topic-tracing", "query": {"topics": ["reporting-refresh"]}},
        )
        source_ids = {
            line["conversation_id"] for row in surviving["timeline"] for line in row["evidence"]
        }
        lifecycle = {
            "new_ingestions_completed": sum(
                row["completed"] and row["changed"] for row in ingestions
            ),
            "new_ingestions_expected": len(ingestions),
            "duplicate_was_idempotent": duplicate["completed"]
            and not duplicate["changed"]
            and stable,
            "interrupted_stage_recovered": ingestions[0]["completed"]
            and ingestions[0]["retries"] == 1,
            "deletion_removed_target_and_preserved_other_sources": "plan-alpha" not in source_ids
            and "21" in source_ids,
        }
        cases[RECOVERY_CASE] = _offline_recovery(Path(directory) / "recovery")
        passed = (
            all(case["passed"] for case in cases.values())
            and lifecycle["new_ingestions_completed"] == len(ingestions)
            and all(value for value in lifecycle.values())
        )
        return {
            "mode": "deterministic",
            "fixture_sha256": snapshot["fixture_sha256"],
            "passed": passed,
            "lifecycle": lifecycle,
            "successful_tool_calls": kite.successes,
            "attempted_tool_calls": kite.calls,
            "cases": cases,
        }


def _answer_schema(case: dict, *, recovery: bool = False) -> dict:
    def fields(properties: dict) -> dict:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    string = {"type": "string"}
    citation = fields({"conversation_id": string, "segment_id": string})
    claim = fields(
        {
            "value": {"anyOf": [string, {"type": "null"}, {"type": "array", "items": string}]},
            "citations": {"type": "array", "items": citation},
        }
    )
    answer = {"run_id": string, "claims": fields(dict.fromkeys(case["fields"], claim))}
    if recovery:
        answer["ingestion_run_id"] = string
    return fields(answer)


def prepare(directory: Path, name: str, case: dict, fixture: dict) -> None:
    directory.mkdir(mode=0o700)
    kite = Kite(directory / "memory")
    snapshot = {"case": name, **_snapshot(kite, _seed(kite, fixture))}
    (directory / "baseline.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (directory / "ANSWER.schema.json").write_text(
        json.dumps(_answer_schema(case), indent=2),
        encoding="utf-8",
    )
    fields = "\n".join(f"- `{name}`: {description}" for name, description in case["fields"].items())
    (directory / "TASK.md").write_text(
        f"""# KITE synthetic agent task

Run `kite-tool resources` and read the project instructions it reports.
Use the preloaded synthetic workspace with `kite-tool --workspace ./memory`.
Use the appropriate KITE workflow, inspect its stage skills and complete the run.
Read only from the seeded memory. Keep all writes within this task directory.

{case["task"]}

Return a JSON object with `run_id` for the completed workflow and `claims`.
Use these exact claim keys and values.

{fields}

Each claim is an object with `value` and `citations`. Each citation has
`conversation_id` and `segment_id` from the KITE report.
""",
        encoding="utf-8",
    )


def _prepare_recovery(directory: Path, fixture: dict) -> tuple[Kite, dict]:
    directory.mkdir(mode=0o700)
    kite = Kite(directory / "memory")
    export = directory / "transcript.json"
    export.write_text(json.dumps(fixture["export"]), encoding="utf-8")
    source = kite.call("file-source", str(export), "--format", "memoket")
    run_id = kite.call("start", "memory-ingestion", payload=source)["run_id"]
    normalized = kite.stage(run_id, "normalize")["value"]
    kite.stage(run_id, "annotate", error="missing_input")
    snapshot = {
        "case": RECOVERY_CASE,
        "fixture_sha256": hashlib.sha256(RECOVERY_FIXTURE.read_bytes()).hexdigest(),
        "ingestion_run_digest": hashlib.sha256(run_id.encode()).hexdigest(),
        "source_checksum": normalized["checksum"],
        "source_corpus_bytes": sum(
            len(line["text"].encode()) for line in normalized["conversation"]["segments"]
        ),
    }
    (directory / "baseline.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (directory / "ANSWER.schema.json").write_text(
        json.dumps(_answer_schema(fixture["case"], recovery=True), indent=2), encoding="utf-8"
    )
    (directory / "TASK.md").write_text(
        """# Resume a Memoket import

Use the installed KITE CLI in `./memory` and read its packaged operating guide.
`transcript.json` is the raw export. A prior process normalized it, then stopped
when annotation input was missing. Discover and resume that ingestion run.
Keep its original source and segment IDs. Add one confirmed fact under topic
`release review` using `review-day` as the state key. Complete the import
and trace that topic with `topic-tracing`. Keep writes within the task directory.
Use the CLI for workspace access and keep the baseline and export intact.

Return the ingestion run ID as `ingestion_run_id`, the trace run ID as `run_id`,
and the confirmed English weekday as claim `review_day`, with its original
conversation and segment IDs. Use `ANSWER.schema.json` for the response.
""",
        encoding="utf-8",
    )
    return kite, snapshot


def _score_recovery(kite: Kite, fixture: dict, answer: dict, snapshot: dict) -> dict:
    case = fixture["case"]
    integrity = kite.call("integrity")
    result = _grade(kite, case, answer, {**snapshot, **integrity})
    result.pop("workspace_unchanged")
    resumed = False
    ingestion_attempts = ingestion_retries = 0
    run = answer.get("ingestion_run_id")
    if (
        isinstance(run, str)
        and hashlib.sha256(run.encode()).hexdigest() == snapshot["ingestion_run_digest"]
    ):
        try:
            status = kite.call("status", run)
            stages = status["stages"]
            ingestion_attempts = sum(stage["attempts"] for stage in stages.values())
            ingestion_retries = sum(stage["retry_count"] for stage in stages.values())
            resumed = (
                status["pipeline_id"] == "memory-ingestion"
                and status["state"] == "completed"
                and status["snapshot_current"]
                and stages["normalize"]["attempts"] == 1
                and stages["annotate"]["attempts"] == 2
                and stages["commit"]["attempts"] == 1
                and stages["verify"]["state"] == "completed"
            )
        except BenchmarkError:
            resumed = False
    sources = kite.call("sources")["sources"]
    expected_source = {
        "source": "memoket",
        "conversation_id": fixture["export"]["data"]["id"],
        "current_checksum": snapshot["source_checksum"],
        "revisions": 1,
    }
    write_verified = (
        integrity["memory_version"] == 1
        and integrity["facts"] == 1
        and sources == [expected_source]
    )
    report_verified = False
    if result["pipeline_completed"]:
        report = kite.call("artifact", answer["run_id"], "report")["value"]
        quoted = fixture["export"]["data"]["segments"][0]["sentences"][0]
        report_verified = _reference_answer(case, answer["run_id"], report)["claims"] == case[
            "expected"
        ] and any(
            line["text"] == quoted["text"]
            and line["segment_id"] == quoted["line_id"]
            and line["conversation_id"] == expected_source["conversation_id"]
            for row in report["timeline"]
            for line in row["evidence"]
        )
    result.update(
        interrupted_run_recovered=resumed,
        ingestion_stage_attempts=ingestion_attempts,
        ingestion_stage_retries=ingestion_retries,
        expected_write_verified=write_verified,
        report_evidence_verified=report_verified,
    )
    result["passed"] &= resumed and write_verified and report_verified
    return result


def _offline_recovery(directory: Path) -> dict:
    fixture = json.loads(RECOVERY_FIXTURE.read_text(encoding="utf-8"))
    kite, snapshot = _prepare_recovery(directory, fixture)
    run = kite.call("runs")["runs"][0]["run_id"]
    kite.stage(run, "annotate", payload={"annotations": fixture["annotations"]})
    kite.stage(run, "commit")
    kite.stage(run, "verify")
    report = kite.trace("recovered-trace", fixture["case"])
    answer = _reference_answer(fixture["case"], "recovered-trace", report)
    answer["ingestion_run_id"] = run
    return {
        "fixture_sha256": snapshot["fixture_sha256"],
        **_score_recovery(kite, fixture, answer, snapshot),
    }


def main(argv=None) -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("offline", help="run deterministic lifecycle and retrieval cases")
    for command in ("prepare", "score"):
        action = sub.add_parser(command)
        action.add_argument("case", choices=[*fixture["cases"], RECOVERY_CASE])
        action.add_argument("directory", type=Path)
        if command == "score":
            action.add_argument("answer", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "offline":
            result = offline(fixture)
        else:
            recovery = args.case == RECOVERY_CASE
            fixture_path = RECOVERY_FIXTURE if recovery else FIXTURE
            if recovery:
                recovery_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            if args.command == "prepare":
                if recovery:
                    _prepare_recovery(args.directory, recovery_fixture)
                else:
                    prepare(args.directory, args.case, fixture["cases"][args.case], fixture)
                print(args.directory / "TASK.md")
                return 0
            snapshot = json.loads((args.directory / "baseline.json").read_text(encoding="utf-8"))
            if snapshot["case"] != args.case:
                raise BenchmarkError("workspace was prepared for a different case")
            if snapshot["fixture_sha256"] != hashlib.sha256(fixture_path.read_bytes()).hexdigest():
                raise BenchmarkError("case was prepared with a different fixture")
            answer = json.loads(args.answer.read_text(encoding="utf-8"))
            if not isinstance(answer, dict):
                raise BenchmarkError("answer must be a JSON object")
            kite = Kite(args.directory / "memory")
            metrics = (
                _score_recovery(kite, recovery_fixture, answer, snapshot)
                if recovery
                else _grade(kite, fixture["cases"][args.case], answer, snapshot)
            )
            result = {
                "mode": "host-agent",
                "case": args.case,
                "fixture_sha256": snapshot["fixture_sha256"],
                **metrics,
            }
    except (BenchmarkError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Agent benchmark failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
