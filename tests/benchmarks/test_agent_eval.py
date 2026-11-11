"""The synthetic benchmark scores claims against verified source citations."""

import hashlib
import json
from copy import deepcopy

from benchmarks.agent.__main__ import (
    DELETION_CASE,
    DELETION_TARGET,
    FIXTURE,
    RECOVERY_FIXTURE,
    Kite,
    _deletion_trace,
    _grade,
    _offline_update,
    _prepare_deletion,
    _prepare_recovery,
    _reference_answer,
    _score_deletion,
    _score_recovery,
    _seed,
    _snapshot,
    _trace_expectations,
    main,
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
    expected, _ = _trace_expectations(fixture)

    def grade(name, query, run):
        case = fixture["cases"][name]
        report = kite.trace(run, {**case, "query": {**case["query"], **query}})
        return _grade(
            kite,
            case,
            _reference_answer(case, run, report),
            baseline,
            expected_trace=expected[name],
        )

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


def test_trace_grade_checks_missing_and_extra_facts_in_the_same_sources(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    kite = Kite(tmp_path / "memory")
    baseline = _snapshot(kite, _seed(kite, fixture))
    expected, _ = _trace_expectations(fixture)
    cases = fixture["cases"]
    format_report = kite.trace("format", cases["format-decision"])
    owner_report = kite.trace("owner", cases["owner-handoff"])
    proposal = next(row for row in format_report["timeline"] if row["status"] == "proposal")
    original_call = kite.call

    for name, run, report, timeline, recall, precision in (
        (
            "format-decision",
            "format",
            format_report,
            [row for row in format_report["timeline"] if row != proposal],
            6 / 7,
            1,
        ),
        (
            "format-decision",
            "format",
            format_report,
            [*format_report["timeline"], proposal],
            1,
            7 / 8,
        ),
        ("owner-handoff", "owner", owner_report, [*owner_report["timeline"], proposal], 1, 2 / 3),
    ):

        def altered_call(*args, **kwargs):
            result = original_call(*args, **kwargs)
            if args == ("artifact", run, "report"):
                result["value"]["timeline"] = timeline
                result["value"]["matched_count"] = len(timeline)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(kite, "call", altered_call)
            case = cases[name]
            score = _grade(
                kite,
                case,
                _reference_answer(case, run, report),
                baseline,
                expected_trace=expected[name],
            )
        assert score["source_scope_correct"] and score["pipeline_completed"]
        assert score["supported_claims"] == score["expected_claims"]
        assert score["trace_fact_recall"] == recall and score["trace_fact_precision"] == precision
        assert not score["trace_facts_exact"] and not score["passed"]


def test_trace_grade_checks_chronology_and_decision_state(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    kite = Kite(tmp_path / "memory")
    baseline = _snapshot(kite, _seed(kite, fixture))
    name = "format-decision"
    case = fixture["cases"][name]
    expected, _ = _trace_expectations(fixture)
    original = kite.trace(name, case)
    answer = _reference_answer(case, name, original)
    original_call = kite.call

    def grade(report):
        def altered_call(*args, **kwargs):
            if args == ("artifact", name, "report"):
                return {"value": report}
            return original_call(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(kite, "call", altered_call)
            return _grade(kite, case, answer, baseline, expected_trace=expected[name])

    tied = deepcopy(original)
    tied["timeline"][:2] = reversed(tied["timeline"][:2])
    score = grade(tied)
    assert score["expected_ordered_pairs"] == score["correctly_ordered_pairs"] == 20
    assert score["temporal_ordering_accuracy"] == 1.0 and score["passed"]

    wrong_order = deepcopy(original)
    wrong_order["timeline"][2:4] = reversed(wrong_order["timeline"][2:4])
    score = grade(wrong_order)
    assert score["trace_facts_exact"] and score["trace_claims_correct"]
    assert score["correctly_ordered_pairs"] == 19
    assert score["temporal_ordering_accuracy"] == 19 / 20
    assert not score["trace_order_correct"] and not score["passed"]

    missing = deepcopy(original)
    missing["timeline"].pop(2)
    score = grade(missing)
    assert score["correctly_ordered_pairs"] == 14
    assert score["temporal_ordering_accuracy"] == 14 / 20
    assert not score["trace_order_correct"] and not score["passed"]

    wrong_state = deepcopy(original)
    pdf = next(row for row in wrong_state["timeline"] if row["state"] == "PDF")
    pdf["status"] = "proposal"
    score = grade(wrong_state)
    assert score["trace_facts_exact"] and score["trace_order_correct"]
    assert score["supported_claims"] == 2 and score["trace_supported_claims"] == 1
    assert not score["trace_claims_correct"] and not score["passed"]


def test_host_grade_counts_retries_in_an_unfinished_trace(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    case = fixture["cases"]["dated-timeline"]
    expected, _ = _trace_expectations(fixture)
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
    score = _grade(kite, case, answer, baseline, expected_trace=expected["dated-timeline"])
    assert not score["pipeline_completed"] and not score["passed"]
    assert score["stage_attempts"] == 2 and score["stage_retries"] == 1
    assert score["stage_retry_rate"] == 0.5

    other = _grade(
        kite,
        {**case, "pipeline": "topic-tracing"},
        answer,
        baseline,
        expected_trace=expected["dated-timeline"],
    )
    assert other["stage_attempts"] == other["stage_retries"] == 0
    assert other["stage_retry_rate"] is None


def test_context_comparison_measures_the_prompts_only_when_both_answers_pass(tmp_path, capsys):
    directory = tmp_path / "trial"
    case = "format-decision"
    assert main(["prepare-context", case, str(directory)]) == 0
    capsys.readouterr()
    kite_dir, pasted_dir = directory / "kite", directory / "pasted"
    kite_prompt = (kite_dir / "TASK.md").read_bytes()
    pasted_prompt = (pasted_dir / "TASK.md").read_bytes()
    assert b"catering spreadsheet" in pasted_prompt and b"catering spreadsheet" not in kite_prompt
    assert set(path.name for path in pasted_dir.iterdir()) == {"TASK.md", "ANSWER.schema.json"}
    schema = json.loads((pasted_dir / "ANSWER.schema.json").read_text(encoding="utf-8"))
    assert schema["required"] == ["claims"]

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case_fixture = fixture["cases"][case]
    kite = Kite(kite_dir / "memory")
    run = "with-kite"
    report = kite.trace(run, case_fixture)
    answer = _reference_answer(case_fixture, run, report)
    (kite_dir / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
    pasted_answer = pasted_dir / "answer.json"
    pasted_answer.write_text(json.dumps({"claims": answer["claims"]}), encoding="utf-8")

    command = ["score-context", case, str(directory)]
    assert main(command) == 0
    score = json.loads(capsys.readouterr().out)
    assert score["kite_prompt_bytes"] == len(kite_prompt)
    assert score["pasted_prompt_bytes"] == len(pasted_prompt)
    assert score["pasted_prompt_sha256"] == hashlib.sha256(pasted_prompt).hexdigest()
    saved = len(pasted_prompt) - len(kite_prompt)
    assert score["user_prompt_bytes_saved"] == saved > 0
    assert score["user_prompt_context_reduction"] == saved / len(pasted_prompt)

    answer["claims"]["confirmed_format"]["citations"] = []
    pasted_answer.write_text(json.dumps({"claims": answer["claims"]}), encoding="utf-8")
    assert main(command) == 1
    score = json.loads(capsys.readouterr().out)
    assert score["kite"]["passed"] and not score["pasted"]["passed"]
    assert score["user_prompt_context_reduction"] is None

    (pasted_dir / "TASK.md").write_bytes(pasted_prompt + b"\n")
    assert main(command) == 2
    assert "prepared prompt differs" in capsys.readouterr().err


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
    paraphrased = deepcopy(fixture["annotations"])
    paraphrased["facts"][0]["content"] = "The release review was confirmed for Thursday."
    kite.stage(run, "annotate", payload={"annotations": paraphrased})
    kite.stage(run, "commit")
    kite.stage(run, "verify")
    report = kite.trace("trace", fixture["case"])
    answer = _reference_answer(fixture["case"], "trace", report)
    answer["ingestion_run_id"] = run
    result = _score_recovery(kite, fixture, answer, baseline)
    assert result["interrupted_run_recovered"] and result["report_evidence_verified"]
    assert result["trace_facts_exact"]
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
    expected, _ = _trace_expectations(fixture)
    result = _score_deletion(
        kite, case, answer, baseline, fixture, expected_trace=expected[DELETION_CASE]
    )
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

    expected, _ = _trace_expectations(fixture)
    result = _score_deletion(
        kite, case, answer, baseline, fixture, expected_trace=expected[DELETION_CASE]
    )
    assert result["expected_sources_preserved"] and result["post_deletion_evidence_verified"]
    assert result["approved_deletion_applied"] and result["prior_report_invalidated"]
    provenance = result["surviving_fact_provenance"]
    assert provenance["observed_facts"] == provenance["expected_facts"] == 7
    assert provenance["facts_with_expected_provenance"] == 6
    assert not provenance["exact_match"] and not result["passed"]


def test_update_benchmark_requires_the_historical_source_text(tmp_path, monkeypatch):
    original_call = Kite.call

    def changed_history(kite, *args, **kwargs):
        result = original_call(kite, *args, **kwargs)
        if args[0] == "source-lines":
            result["segments"][0]["text"] = "The historical line was replaced."
        return result

    monkeypatch.setattr(Kite, "call", changed_history)
    result = _offline_update(tmp_path / "memory")
    assert result["history_preserved"] and result["current_state_updated"]
    assert result["index_consistent"] and result["fact_provenance_verified"]
    assert not result["historical_source_readable"] and not result["passed"]
