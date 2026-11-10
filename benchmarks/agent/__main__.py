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
DELETION_CASE = "approved-deletion"
DELETION_TARGET = {"source": "text", "conversation_id": "plan-alpha"}


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

        approval, baseline = _deletion_preview(kite, "format-decision", snapshot)
        deletion = approval["run_id"]
        kite.call("stage", deletion, "apply", "--approve", approval["plan"]["approval_token"])
        case = _deletion_trace(fixture)
        surviving = kite.trace("after-delete", case)
        answer = _reference_answer(case, "after-delete", surviving)
        answer["deletion_run_id"] = deletion
        cases[DELETION_CASE] = _score_deletion(kite, case, answer, baseline)
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
            "deletion_removed_target_and_preserved_other_sources": cases[DELETION_CASE][
                "expected_sources_preserved"
            ]
            and cases[DELETION_CASE]["post_deletion_evidence_verified"],
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


def _answer_schema(case: dict, *, write_run: str | None = None) -> dict:
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
    if write_run:
        answer[write_run] = string
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
        json.dumps(_answer_schema(fixture["case"], write_run="ingestion_run_id"), indent=2),
        encoding="utf-8",
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


def _deletion_trace(fixture: dict) -> dict:
    case = fixture["cases"]["format-decision"]
    return {
        **case,
        "query": {"topics": ["reporting-refresh"]},
        "expected_source_ids": [
            identity
            for identity in case["expected_source_ids"]
            if identity != DELETION_TARGET["conversation_id"]
        ],
    }


def _deletion_preview(kite: Kite, prior_run: str, snapshot: dict) -> tuple[dict, dict]:
    run = kite.call("start", "memory-deletion", payload={"target": DELETION_TARGET})["run_id"]
    plan = kite.stage(run, "preview")["value"]
    if plan["target"] != DELETION_TARGET or plan["revisions"] != 1:
        raise BenchmarkError("fixture did not produce the expected deletion preview")
    sources = kite.call("sources")["sources"]
    integrity = kite.call("integrity")
    return {"run_id": run, "plan": plan}, {
        **snapshot,
        "case": DELETION_CASE,
        "deletion_run_id": run,
        "operation_id": plan["approval_token"],
        "removed_revisions": plan["revisions"],
        "removed_facts": plan["facts"],
        "prior_run_id": prior_run,
        "expected_revisions": integrity["revisions"] - plan["revisions"],
        "expected_facts": integrity["facts"] - plan["facts"],
        "expected_sources": [
            source
            for source in sources
            if any(source[key] != value for key, value in DELETION_TARGET.items())
        ],
    }


def _prepare_deletion(directory: Path, fixture: dict) -> tuple[Kite, dict]:
    directory.mkdir(mode=0o700)
    kite = Kite(directory / "memory")
    snapshot = _snapshot(kite, _seed(kite, fixture))
    prior_run = "prior-report"
    kite.trace(prior_run, fixture["cases"]["format-decision"])
    approval, baseline = _deletion_preview(kite, prior_run, snapshot)
    (directory / "approved-preview.json").write_text(json.dumps(approval), encoding="utf-8")
    (directory / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    case = _deletion_trace(fixture)
    (directory / "ANSWER.schema.json").write_text(
        json.dumps(_answer_schema(case, write_run="deletion_run_id"), indent=2), encoding="utf-8"
    )
    (directory / "TASK.md").write_text(
        """# Verify an approved synthetic conversation deletion

Read the installed KITE operating guide via `kite-tool resources`. Use only
the synthetic workspace at `./memory` and access it through the installed CLI.

The benchmark operator authorizes the exact preview in
`approved-preview.json` for this synthetic task. Inspect the saved preview
in that run and check it against the authorized preview before applying it.
If the preview differs, stop and report the discrepancy. Apply only that
approved operation. Preserve the other conversations and keep the task files intact.

Verify the deletion via the audit and confirm that the earlier report
`prior-report` is invalidated. Trace the canonical `reporting-refresh`
topic again. Report which export format was confirmed and which was rejected,
using their short uppercase names and citing the surviving evidence.

Return the deletion run ID as `deletion_run_id`, the post-deletion trace ID as
`run_id`, and the claims `confirmed_format` and `rejected_format`.
Use `ANSWER.schema.json` for the response.
""",
        encoding="utf-8",
    )
    return kite, baseline


def _score_deletion(kite: Kite, case: dict, answer: dict, snapshot: dict) -> dict:
    integrity = kite.call("integrity")
    result = _grade(kite, case, answer, {**snapshot, **integrity})
    result.pop("workspace_unchanged")
    run = snapshot["deletion_run_id"]
    audit = kite.call("deletion-audit")["deletions"]
    expected_version = snapshot["memory_version"] + 1
    audited = len(audit) == 1 and all(
        audit[0].get(key) == value
        for key, value in {
            "operation": snapshot["operation_id"],
            "version": expected_version,
            "revision_count": snapshot["removed_revisions"],
            "fact_count": snapshot["removed_facts"],
        }.items()
    )
    applied = False
    if answer.get("deletion_run_id") == run:
        try:
            status = kite.call("status", run)
            receipt = kite.call("artifact", run, "apply")["value"]
            applied = (
                status["pipeline_id"] == "memory-deletion"
                and status["state"] == "completed"
                and status["stages"]["preview"]["state"] == "redacted"
                and receipt["memory_version"] == expected_version
                and receipt["deleted_revisions"] == snapshot["removed_revisions"]
                and receipt["operation_id"] == snapshot["operation_id"]
            )
        except BenchmarkError:
            applied = False
    try:
        kite.call("artifact", snapshot["prior_run_id"], "report", error="invalidated_run")
        invalidated = True
    except BenchmarkError:
        invalidated = False
    preserved = (
        integrity["memory_version"] == expected_version
        and integrity["revisions"] == snapshot["expected_revisions"]
        and integrity["facts"] == snapshot["expected_facts"]
        and kite.call("sources")["sources"] == snapshot["expected_sources"]
    )
    report_verified = False
    if result["pipeline_completed"]:
        trace_run = answer["run_id"]
        report = kite.call("artifact", trace_run, "report")["value"]
        resolved = kite.call("artifact", trace_run, "resolve")["value"]
        source_ids = {
            line["conversation_id"] for row in report["timeline"] for line in row["evidence"]
        }
        report_verified = (
            resolved["query"] == case["query"]
            and source_ids == set(case["expected_source_ids"])
            and _reference_answer(case, trace_run, report)["claims"] == case["expected"]
        )
    result.update(
        approved_deletion_applied=applied and audited,
        prior_report_invalidated=invalidated,
        expected_sources_preserved=preserved,
        post_deletion_evidence_verified=report_verified,
    )
    result["passed"] &= applied and audited and invalidated and preserved and report_verified
    return result


def main(argv=None) -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("offline", help="run deterministic lifecycle and retrieval cases")
    for command in ("prepare", "score"):
        action = sub.add_parser(command)
        action.add_argument("case", choices=[*fixture["cases"], RECOVERY_CASE, DELETION_CASE])
        action.add_argument("directory", type=Path)
        if command == "score":
            action.add_argument("answer", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "offline":
            result = offline(fixture)
        else:
            recovery = args.case == RECOVERY_CASE
            deletion = args.case == DELETION_CASE
            fixture_path = RECOVERY_FIXTURE if recovery else FIXTURE
            if recovery:
                recovery_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            if args.command == "prepare":
                if recovery:
                    _prepare_recovery(args.directory, recovery_fixture)
                elif deletion:
                    _prepare_deletion(args.directory, fixture)
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
            if recovery:
                metrics = _score_recovery(kite, recovery_fixture, answer, snapshot)
            elif deletion:
                metrics = _score_deletion(kite, _deletion_trace(fixture), answer, snapshot)
            else:
                metrics = _grade(kite, fixture["cases"][args.case], answer, snapshot)
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
