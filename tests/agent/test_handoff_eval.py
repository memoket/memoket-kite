import json
import os
from types import SimpleNamespace

import pytest

from benchmarks.agent import handoff_eval


def test_receipts_require_a_matching_symbolic_mcp_call_and_compiled_plan(tmp_path):
    path = tmp_path / "symbolic.jsonl"
    reference = "review#L1"
    quote = "We approved nine signups."
    text = f"[{reference}] {quote}"
    delivery = {
        "text": text,
        "receipt": {
            "engine": "memoket_kite.core.algebra.execute_plan",
            "compiler": "host_inline_plan",
            "memory_version": 2,
            "plan": {"queries": [{"select": "lines", "where": {"grep": "signups"}}]},
            "trace": [{"stage": "results", "hits": 1}],
            "spans": [
                {"evidence": [{"conversation_id": "review", "segment_id": "L1", "text": quote}]}
            ],
        },
    }
    call = {
        "server": "kite_context",
        "tool": "recall_conversation_evidence",
        "status": "completed",
        "result": {"content": [{"type": "text", "text": text}]},
    }
    bundle = {"manifest": {"memory_version": 2}, "refs": {reference}}
    path.write_text(json.dumps(delivery) + "\n", encoding="utf-8")
    assert handoff_eval._observed_receipts(path, {"mcp_calls": [call]}, bundle) == [
        {**delivery, "reference_ids": [reference]}
    ]
    path.write_text((json.dumps(delivery) + "\n") * 2, encoding="utf-8")
    assert len(handoff_eval._observed_receipts(path, {"mcp_calls": [call]}, bundle)) == 1
    for other_call in (
        {**call, "server": "other"},
        {**call, "tool": "read_conversation_citations"},
    ):
        assert not handoff_eval._observed_receipts(path, {"mcp_calls": [other_call]}, bundle)
    for missing in ("compiler", "plan", "trace", "memory_version"):
        invalid_receipt = dict(delivery["receipt"])
        del invalid_receipt[missing]
        invalid = {**delivery, "receipt": invalid_receipt}
        path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
        assert not handoff_eval._observed_receipts(path, {"mcp_calls": [call]}, bundle)


def test_symbolic_summary_requires_matched_arms_and_citations_delivered():
    def trial(arm, repetition, receipts=()):
        return {
            "arm": arm,
            "case": "release",
            "repetition": repetition,
            "passed": True,
            "score": {"passed_claims": 1, "total_claims": 1},
            "first_turn_evidence_fields": int(bool(receipts)),
            "turns": [
                {
                    "original_symbolic_results": receipts,
                    "prompt": "Apply the release settings.",
                    "seconds": 1,
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                }
            ],
        }

    rows = [
        trial(arm, n, receipts if arm == "kite" else ())
        for n, receipts in enumerate(
            ([{"reference_ids": ["release#L2"]}], [], [{"reference_ids": []}])
        )
        for arm in ("kite", "files", "external")
    ]
    rows.append(trial("kite", 3, [{"reference_ids": ["release#L2"]}]))
    assert handoff_eval._summary(rows)["kite"]["passed"] == 4
    verified = handoff_eval._verified_symbolic_summary(rows)
    assert verified["matched_trials"] == 1
    assert verified["groups_without_verified_citations"] == 2
    assert verified["incomplete_groups"] == 1
    assert handoff_eval._verified_symbolic_summary(rows[3:])["matched_trials"] == 0
    assert {arm: data["total_tokens"] for arm, data in verified["arms"].items()} == {
        "kite": 110,
        "files": 110,
        "external": 110,
    }


@pytest.mark.parametrize("request_history", [True, False])
def test_handoff_responds_only_to_the_agents_request_and_resumes_same_chat(
    tmp_path, monkeypatch, request_history
):
    seed = tmp_path / "seed"
    seed.mkdir()
    policy = seed / "policy.py"
    policy.write_text("value = None\n", encoding="utf-8")
    os.utime(policy, (1_600_000_000, 1_600_000_000))
    case = {
        "id": "rollout",
        "seed": seed,
        "task": "Apply the approved rollout limit.",
        "source_format": "text",
        "bundle": {"refs": {"review#text-line-1"}},
        "verifier": tmp_path / "verify.py",
        "handoff_refs": ["review#text-line-1"],
        "handoff_message": "[review#text-line-1] We approved 9 signups.",
        "evidence": {"limit": ["review#text-line-1"]},
    }
    activity = []

    def codex(_binary, _model, project, prompt, **options):
        assert not (project / "sources").exists()
        assert (project / "policy.py").stat().st_mtime > policy.stat().st_mtime
        assert (project / "policy.py").read_bytes() == policy.read_bytes()
        assert options["persist_session"] is True
        assert options["codex_options"] == ("--disable", "apps")
        activity.append((prompt, options["resume_session"]))
        if len(activity) == 1 and request_history:
            return {
                "answer": "Meeting context request: Could you share the approval?",
                "session_id": "original",
                "error": None,
            }
        return {
            "answer": "Implemented the limit [review#text-line-1].",
            "session_id": "original",
            "error": None,
            "commands": [{"exit_code": 0, "output": "Ran 1 test in 0.001s\n\nOK"}],
        }

    def verify(_path, _project, checks):
        assert checks == {"limit"}
        # A passing code check alone never triggers a handoff, nor counts as evidence.
        activity.append("verify")
        return {"limit": True}, None

    monkeypatch.setattr(handoff_eval, "codex_trial", codex)
    monkeypatch.setattr(handoff_eval, "_verify", verify)
    row = handoff_eval._trial(SimpleNamespace(codex="codex", model="trial"), case, "external", 1)
    expected = [(case["task"], None)]
    if request_history:
        expected.append((case["handoff_message"], "original"))
    assert activity == [*expected, "verify"]
    assert row["passed"] is request_history
    assert len(row["turns"]) == len(expected)


def test_handoff_request_ignores_errors_and_nonrequests():
    turn = {"session_id": "chat", "error": None}
    assert (
        handoff_eval._handoff_request({**turn, "answer": "The code needs meeting context."}) is None
    )
    assert (
        handoff_eval._handoff_request(
            {**turn, "error": "timeout", "answer": "Meeting context request: Notes?"}
        )
        is None
    )
    assert (
        handoff_eval._handoff_request(
            {**turn, "answer": "Meeting context request: Notes?\nMeeting context request: Date?"}
        )
        is None
    )
