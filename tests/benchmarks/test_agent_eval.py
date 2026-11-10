"""The synthetic benchmark scores claims against verified source citations."""

import json
from copy import deepcopy

from benchmarks.agent.__main__ import FIXTURE, score_claims


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
