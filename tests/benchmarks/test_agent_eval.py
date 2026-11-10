"""The synthetic benchmark scores claims against verified source citations."""

import json
from copy import deepcopy

from benchmarks.agent.__main__ import (
    DELETION_TARGET,
    FIXTURE,
    RECOVERY_FIXTURE,
    _deletion_trace,
    _prepare_deletion,
    _prepare_recovery,
    _reference_answer,
    _score_deletion,
    _score_recovery,
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
    assert not result["interrupted_run_recovered"]
    assert not result["passed"]


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
    result = _score_deletion(kite, case, answer, baseline)
    assert result["expected_sources_preserved"] and result["post_deletion_evidence_verified"]
    assert result["prior_report_invalidated"]
    assert not result["approved_deletion_applied"] and not result["passed"]
