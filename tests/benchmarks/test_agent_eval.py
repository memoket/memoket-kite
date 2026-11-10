"""The synthetic benchmark scores claims against verified source citations."""

import json
from copy import deepcopy

from benchmarks.agent.__main__ import (
    DELETION_TARGET,
    FIXTURE,
    RECOVERY_FIXTURE,
    Kite,
    _deletion_trace,
    _grade,
    _prepare_deletion,
    _prepare_recovery,
    _reference_answer,
    _score_deletion,
    _score_recovery,
    _seed,
    _snapshot,
    score_claims,
)


def test_correct_claims_require_the_actual_report_and_expected_citations():
    case = json.loads(FIXTURE.read_text())["cases"]["format-decision"]
    expected = deepcopy(case["expected"])
    lines = [
        {**item, "text": "synthetic source"}
        for claim in expected.values()
        for item in claim["citations"]
    ]
    report = {"timeline": [{"evidence": lines}]}
    assert score_claims(case, expected, report)["passed"]
    assert not score_claims(case, expected, None)["passed"]

    expected["confirmed_format"]["citations"] = expected["rejected_format"]["citations"]
    score = score_claims(case, expected, report)
    assert score["supported_claims"] == 1
    assert score["unsupported_claims"] == 1
    assert not score["passed"]


def test_unknown_claims_and_ordered_dates_are_graded():
    case = json.loads(FIXTURE.read_text())["cases"]["dated-timeline"]
    expected = deepcopy(case["expected"])
    lines = [{**line, "text": "synthetic source"} for line in expected["source_dates"]["citations"]]
    lines.append(
        {"conversation_id": "21", "segment_id": "choose-pdf", "text": "same-date alternative"}
    )
    report = {"timeline": [{"evidence": lines}]}

    expected["source_dates"]["citations"][1]["segment_id"] = "choose-pdf"
    assert score_claims(case, expected, report)["passed"]

    expected["invented"] = {"value": "unknown", "citations": []}
    assert score_claims(case, expected, report)["unsupported_claims"] == 1
    expected["source_dates"]["value"].reverse()
    score = score_claims(case, expected, report)
    assert score["expected_citations_found"] == 2
    assert score["unsupported_claims"] == 2
    assert not score["passed"]


def test_citation_precision_counts_duplicate_malformed_and_extra_claim_citations():
    relevant = {"conversation_id": "planning", "segment_id": "decision"}
    unrelated = {"conversation_id": "planning", "segment_id": "question"}
    claim = {"value": "approved", "citations": [relevant]}
    case = {"expected": {"decision": claim}}
    report = {"timeline": [{"evidence": [{**relevant, "text": "Approved."}]}]}
    score = score_claims(case, {}, report)
    assert score["evidence_precision"] is None
    assert score["evidence_recall"] == 0.0
    assert score["unsupported_claim_rate"] is None

    submitted = {
        "decision": {**claim, "citations": [relevant, relevant, None, unrelated]},
        "invented": {"value": "unknown", "citations": [relevant]},
    }
    score = score_claims(case, submitted, report)
    assert score["submitted_citations"] == 5
    assert score["expected_citations_found"] == 1
    assert score["evidence_precision"] == 0.2
    assert score["evidence_recall"] == 1.0
    assert score["unsupported_claim_rate"] == 1.0
    assert score["cited_evidence_bytes"] == len(b"Approved.")
    assert not score["passed"]


def test_overlapping_citation_requirements_need_distinct_evidence():
    first = {"conversation_id": "planning", "segment_id": "first"}
    second = {"conversation_id": "planning", "segment_id": "second"}
    answer = {"decision": {"value": "approved", "citations": [first, second]}}
    case = {"expected": answer, "allow_citations": {"decision": [[list(first.values())]] * 2}}
    report = {"timeline": [{"evidence": [{**line, "text": "source"} for line in (first, second)]}]}
    score = score_claims(case, answer, report)
    assert score["expected_citations_found"] == 1
    assert score["evidence_precision"] == score["evidence_recall"] == 0.5
    assert not score["passed"]

    case["allow_citations"]["decision"][0] = [list(first.values()), list(second.values())]
    score = score_claims(case, answer, report)
    assert score["expected_citations_found"] == 2
    assert score["evidence_precision"] == score["evidence_recall"] == 1.0
    assert score["passed"]


def test_untrusted_transcript_requires_original_text_and_cited_decision():
    case = json.loads(FIXTURE.read_text())["cases"]["untrusted-transcript"]
    decision = deepcopy(case["expected"])
    line = dict(case["required_report_line"])
    report = {"timeline": [{"evidence": [line]}]}
    assert score_claims(case, decision, report)["passed"]

    wrong = deepcopy(decision)
    wrong["confirmed_routing"]["value"] = "permanent queue"
    score = score_claims(case, wrong, report)
    assert score["untrusted_evidence_preserved"] and not score["passed"]

    line["text"] = line["text"].splitlines()[0]
    score = score_claims(case, decision, report)
    assert score["supported_claims"] == 1
    assert not score["untrusted_evidence_preserved"] and not score["passed"]


def test_host_grade_checks_the_trace_scope_even_when_claims_are_correct(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    kite = Kite(tmp_path / "memory")
    baseline = _snapshot(kite, _seed(kite, fixture))

    def grade(name, query, run):
        case = fixture["cases"][name]
        report = kite.trace(run, {**case, "query": {**case["query"], **query}})
        return _grade(kite, case, _reference_answer(case, run, report), baseline)

    alias = grade("dated-timeline", {"topics": ["report overhaul"]}, "alias")
    assert alias["passed"] and alias["date_scope_correct"] and alias["source_scope_correct"]

    year = grade("dated-timeline", {"start_date": "2026-01-01", "end_date": "2026-12-31"}, "year")
    assert year["pipeline_completed"] and year["source_scope_correct"]
    assert year["supported_claims"] == year["expected_claims"]
    assert not year["date_scope_correct"] and not year["passed"]

    broader = grade(
        "format-decision", {"topics": ["report overhaul", "after-hours-policy"]}, "extra-topic"
    )
    assert (
        broader["pipeline_completed"] and broader["supported_claims"] == broader["expected_claims"]
    )
    assert not broader["source_scope_correct"] and not broader["passed"]


def test_host_grade_counts_retries_in_an_unfinished_trace(tmp_path):
    case = json.loads(FIXTURE.read_text())["cases"]["dated-timeline"]
    kite = Kite(tmp_path / "memory")
    kite.start(
        case["pipeline"],
        "invalid-range",
        {"query": {"start_date": "2026-07-31", "end_date": "2026-07-01"}},
    )
    for _ in range(2):
        kite.stage("invalid-range", "resolve", error="invalid_input")
    baseline = _snapshot(kite, [])
    answer = {"run_id": "invalid-range", "claims": {}}
    score = _grade(kite, case, answer, baseline)
    assert not score["pipeline_completed"] and not score["passed"]
    assert score["stage_attempts"] == 2 and score["stage_retries"] == 1
    assert score["stage_retry_rate"] == 0.5

    other = _grade(kite, {**case, "pipeline": "topic-tracing"}, answer, baseline)
    assert other["stage_attempts"] == other["stage_retries"] == 0
    assert other["stage_retry_rate"] is None


def test_recovery_score_rejects_replacing_the_interrupted_run(tmp_path):
    fixture = json.loads(RECOVERY_FIXTURE.read_text())
    directory = tmp_path / "task"
    kite, baseline = _prepare_recovery(directory, fixture)
    source = kite.call("file-source", str(directory / "transcript.json"), "--format", "memoket")
    kite.start("memory-ingestion", "replacement", {**source, "annotations": fixture["annotations"]})
    for stage in ("normalize", "annotate", "commit", "verify"):
        kite.stage("replacement", stage)
    report = kite.trace("trace", fixture["case"])
    answer = _reference_answer(fixture["case"], "trace", report)
    answer["ingestion_run_id"] = "replacement"

    result = _score_recovery(kite, fixture, answer, baseline)
    assert result["expected_write_verified"] and result["report_evidence_verified"]
    assert result["maintenance_runs_unchanged"]
    assert not result["interrupted_run_recovered"]
    assert not result["passed"]


def test_recovery_score_rejects_changes_to_unrelated_runs(tmp_path):
    fixture = json.loads(RECOVERY_FIXTURE.read_text())
    kite, baseline = _prepare_recovery(tmp_path / "task", fixture)
    (interrupted,) = kite.call("runs", "--pipeline", "memory-ingestion", "--unfinished")["runs"]
    run = interrupted["run_id"]
    assert run not in {item["run_id"] for item in kite.call("runs")["runs"]}
    kite.stage("maintenance-1", "verify")
    kite.stage(run, "annotate", payload={"annotations": fixture["annotations"]})
    kite.stage(run, "commit")
    kite.stage(run, "verify")
    report = kite.trace("trace", fixture["case"])
    answer = _reference_answer(fixture["case"], "trace", report)
    answer["ingestion_run_id"] = run
    result = _score_recovery(kite, fixture, answer, baseline)
    assert result["interrupted_run_recovered"] and result["report_evidence_verified"]
    assert not result["maintenance_runs_unchanged"] and not result["passed"]


def test_deletion_score_requires_the_authorized_checkpoint(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    kite, baseline = _prepare_deletion(tmp_path / "task", fixture)
    kite.start("memory-deletion", "replacement", {"target": DELETION_TARGET})
    token = kite.stage("replacement", "preview")["value"]["approval_token"]
    kite.call("stage", "replacement", "apply", "--approve", token)
    case = _deletion_trace(fixture)
    report = kite.trace("trace", case)
    answer = _reference_answer(case, "trace", report)
    answer["deletion_run_id"] = "replacement"
    result = _score_deletion(kite, case, answer, baseline, fixture)
    assert result["expected_sources_preserved"] and result["post_deletion_evidence_verified"]
    assert result["prior_report_invalidated"]
    assert not result["approved_deletion_applied"] and not result["passed"]


def test_deletion_grade_checks_every_surviving_fact_even_if_counts_match(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    altered = deepcopy(fixture)
    catering = next(
        item
        for item in altered["sources"]
        if item["source"]["payload"].get("conversation_id") == "catering"
    )
    catering["source"]["payload"]["text"] = "The catering spreadsheet uses TSV."
    kite, baseline = _prepare_deletion(tmp_path / "task", altered)
    run = baseline["deletion_run_id"]
    kite.call("stage", run, "apply", "--approve", baseline["operation_id"])
    case = _deletion_trace(fixture)
    report = kite.trace("trace", case)
    answer = _reference_answer(case, "trace", report)
    answer["deletion_run_id"] = run

    result = _score_deletion(kite, case, answer, baseline, fixture)
    assert result["expected_sources_preserved"] and result["post_deletion_evidence_verified"]
    assert result["approved_deletion_applied"] and result["prior_report_invalidated"]
    provenance = result["surviving_fact_provenance"]
    assert provenance["observed_facts"] == provenance["expected_facts"] == 7
    assert provenance["facts_with_expected_provenance"] == 6
    assert not provenance["exact_match"] and not result["passed"]
