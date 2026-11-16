import json

from benchmarks.agent.symbolic_submit import verify_delivery
from benchmarks.agent.task_eval import _score, _verified_symbolic_summary


def test_score_resolves_file_links_to_original_citations():
    checks = {"original": True, "revised": True}
    evidence = {"original": ["91001#L7"], "revised": ["update#text-line-2"]}
    valid_refs = {"91001#L7", "update#text-line-2"}
    answer = (
        "[91001#L7](sources/2026-06-01/91001.txt#L7) [Correction](sources/update.md#text-line-2)"
    )

    assert _score(checks, answer, evidence, valid_refs)["full_pass"]

    result = _score(checks, answer + " [Unknown](sources/other.txt#L194)", evidence, valid_refs)
    assert not result["full_pass"]
    assert result["claims"]["original"]["out_of_scope_citations"] == ["other#L194"]


def test_symbolic_delivery_replays_the_observed_budget_and_exact_quotation(tmp_path, monkeypatch):
    launcher = tmp_path / "kite"
    original = "Rollout moved.\n[unknown#L1] A line inside the verbatim quote."
    receipt = {
        "engine": "memoket_kite.core.algebra.execute_plan",
        "compiler": "reused_symbolic_plan",
        "memory_version": 2,
        "reused_plan": {"run_id": "rollout"},
        "plan": {"queries": [{"select": "lines", "where": {"grep": "rollout"}}]},
        "trace": [{"stage": "lines", "hits": 1}],
        "spans": [
            {
                "evidence": [
                    {
                        "conversation_id": "meeting",
                        "segment_id": "L3",
                        "text": original,
                        "speaker_id": "Pat",
                    }
                ],
            }
        ],
    }
    output = f"Workspace v2\n\n[meeting#L3] Pat: {original}"
    commands = []

    def runtime(command, **kwargs):
        commands.append(command)
        return json.dumps(output if "--delivery-bytes" in command else receipt)

    monkeypatch.setattr("benchmarks.agent.symbolic_submit.subprocess.check_output", runtime)
    args = ["symbolic", "--reuse", "rollout", "--delivery-bytes", "5400"]
    hook = {
        "additional_context": "Evidence\n" + json.dumps(output),
        "calls": [{"args": args, "exit_code": 0}],
    }
    verified = verify_delivery(launcher, "rollout", hook, tmp_path, {}, 2)
    assert verified == {
        "verified": True,
        "verification": "legacy_replay",
        "reference_ids": ["meeting#L3"],
        "receipt": receipt,
    }
    assert commands == [[str(launcher), *args], [str(launcher), *args[:3]]]
    assert not verify_delivery(launcher, "rollout", hook, tmp_path, {}, 3)["verified"]
    assert not verify_delivery(launcher, "other", hook, tmp_path, {}, 2)["verified"]
    hook["additional_context"] += "tampered"
    assert not verify_delivery(launcher, "rollout", hook, tmp_path, {}, 2)["verified"]


def test_symbolic_delivery_checks_the_original_receipt_without_replaying(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("a captured delivery must be verified without any replay")

    monkeypatch.setattr("benchmarks.agent.symbolic_submit.subprocess.check_output", unexpected)
    text = "Workspace v2\n\n[meeting#L3] Rollout moved."
    receipt = {
        "engine": "memoket_kite.core.algebra.execute_plan",
        "compiler": "reused_symbolic_plan",
        "memory_version": 2,
        "reused_plan": {"run_id": "rollout"},
        "plan": {"queries": [{"select": "lines", "where": {"grep": "Rollout"}}]},
        "trace": [{"stage": "lines", "hits": 1}],
        "spans": [
            {
                "evidence": [
                    {"conversation_id": "meeting", "segment_id": "L3", "text": "Rollout moved."}
                ]
            }
        ],
    }
    hook = {
        "additional_context": "Evidence\n" + json.dumps(text),
        "calls": [
            {"args": ["symbolic", "--reuse", "rollout", "--delivery-receipt"], "exit_code": 0}
        ],
        "receipts": [{"evidence": text, "receipt": receipt}],
    }
    launcher = tmp_path / "kite"
    verified = verify_delivery(launcher, "rollout", hook, tmp_path, {}, 2)
    assert verified["verification"] == "observed_receipt"
    assert verified["reference_ids"] == ["meeting#L3"]
    assert not verify_delivery(launcher, "rollout", hook, tmp_path, {}, 3)["verified"]
    receipt["trace"] = []
    assert not verify_delivery(launcher, "rollout", hook, tmp_path, {}, 2)["verified"]
    hook["receipts"].clear()
    assert not verify_delivery(launcher, "rollout", hook, tmp_path, {}, 2)["verified"]


def test_symbolic_summary_excludes_all_arms_from_an_unverified_pair():
    def row(case, arm, verified=True):
        score = {"full_pass": True, "total_claims": 1, "passed_claims": 1, "claims": {}}
        return dict(
            case=case,
            arm=arm,
            repetition=1,
            score=score,
            exit_code=0,
            usage={"input_tokens": 100, "output_tokens": 50},
            seconds=4,
            prompt_bytes=20,
            access_prompt_bytes=0,
            command_calls=1,
            tool_stdout_bytes=20,
            hook_context_bytes=10,
            source_tool_bytes=10,
            activation_bytes=18,
            symbolic_delivery={
                "verified": verified,
                "verification": "observed_receipt",
                "covered_claims": 1,
                "observed": {"calls": [{}], "seconds": 0.01},
            },
            turns=[{"score": score}],
            extra_user_turns=0,
        )

    report = _verified_symbolic_summary(
        [
            row("passed", "files"),
            row("passed", "symbolic-submit"),
            row("missing", "files"),
            row("missing", "symbolic-submit", False),
        ],
        ("files", "symbolic-submit"),
    )
    assert report["completed_pairs"] == 1
    assert report["unverified_pairs"] == 1
    assert report["arms"]["files"]["total_tokens"] == 150
    assert report["delivery"]["symbolic-submit"]["covered_claims"] == 1
    assert report["delivery"]["symbolic-submit"]["verification"] == {"observed_receipt": 1}
